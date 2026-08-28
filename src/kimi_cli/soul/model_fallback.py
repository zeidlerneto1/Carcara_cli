"""Automatic model fallback for unavailable models.

When the user's current model is no longer served by its provider endpoint
(e.g. it disappears from the ``/models`` listing, or the provider returns a
404 "model not found" during a generation), this module picks the first
available model of the *same* provider, rebuilds the :class:`LLM`, and
persists it as the new ``default_model``.

The helpers here are intentionally pure / side-effect-free where possible so
they can be unit-tested without network access. The two I/O-touching
functions (:func:`list_provider_available_aliases` and
:func:`persist_default_model`) are the only ones that perform work outside of
their arguments.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from kimi_cli.utils.logging import logger

if TYPE_CHECKING:
    from kimi_cli.auth.oauth import OAuthManager
    from kimi_cli.config import Config
    from kimi_cli.llm import LLM

__all__ = [
    "list_provider_available_aliases",
    "pick_fallback_alias",
    "build_fallback_llm",
    "persist_default_model",
    "maybe_fallback_on_unavailable",
    "MODEL_FALLBACK_CHECK_TIMEOUT_S",
]

#: Hard cap for the availability probe at startup / on-error. The probe must
#: never block the user for long; on timeout we treat the result as "unknown"
#: and do NOT fall back (avoid spurious switches on slow networks).
MODEL_FALLBACK_CHECK_TIMEOUT_S = 8.0


async def _list_provider_model_ids(provider_key: str, config: Config) -> set[str] | None:
    """Return the set of model ids currently served by ``provider_key``.

    Returns ``None`` when the probe could not be completed (no ``base_url``,
    network/HTTP error, or unexpected payload). ``None`` must be treated by
    callers as "unknown" — never as "all models gone".
    """
    from kimi_cli.auth.platforms import (
        get_platform_by_id,
        list_carcara_models,
        list_models,
        parse_managed_provider_key,
    )

    provider = config.providers.get(provider_key)
    if provider is None or not provider.base_url:
        return None

    api_key = provider.api_key.get_secret_value() if provider.api_key else None

    if provider.type == "kimi":
        # Kimi's managed models are listed through the platform endpoint, not
        # the provider base_url.
        platform_id = parse_managed_provider_key(provider_key) or "kimi-code"
        platform = get_platform_by_id(platform_id)
        if platform is None:
            return None
        if not api_key:
            return None
        try:
            infos = await list_models(platform, api_key)
        except Exception as exc:
            logger.debug("Model fallback: listing kimi models failed: {error}", error=exc)
            return None
    else:
        # Carcara and any OpenAI-compatible endpoint expose ``/models`` with a
        # ``data: [{id, ...}]`` shape that ``list_carcara_models`` parses
        # tolerantly (returns [] on HTTP errors).
        try:
            infos = await list_carcara_models(provider.base_url, api_key)
        except Exception as exc:
            logger.debug(
                "Model fallback: listing models for {provider} failed: {error}",
                provider=provider_key,
                error=exc,
            )
            return None

    return {info.id for info in infos}


async def list_provider_available_aliases(
    provider_key: str,
    config: Config,
    *,
    timeout: float = MODEL_FALLBACK_CHECK_TIMEOUT_S,
) -> list[str] | None:
    """Return the available model *aliases* for a provider, or ``None``.

    Probes the provider's ``/models`` endpoint and returns the aliases from
    ``config.models`` (same provider) whose backing model id is present in the
    live listing, preserving ``config.models`` insertion order.

    Returns:
        - A list (possibly empty) when the endpoint was reachable.
        - ``None`` when the probe could not be completed (no ``base_url``,
          network/HTTP error, or timeout). Callers MUST treat ``None`` as
          "unknown" and skip the fallback.
    """
    try:
        available_ids = await asyncio.wait_for(
            _list_provider_model_ids(provider_key, config),
            timeout=timeout,
        )
    except TimeoutError:
        logger.debug(
            "Model fallback: listing models for {provider} timed out",
            provider=provider_key,
        )
        return None
    if available_ids is None:
        return None
    if not available_ids:
        # Endpoint reachable but returned nothing. Treat as "unknown" rather
        # than "all models gone" to avoid wiping the current model on a
        # provider that simply does not implement /models.
        return None

    return [
        alias
        for alias, model_cfg in config.models.items()
        if model_cfg.provider == provider_key and model_cfg.model in available_ids
    ]


def pick_fallback_alias(current_alias: str, available_aliases: list[str]) -> str | None:
    """Return the first available alias that is not the current one.

    Order is preserved from ``available_aliases`` (i.e. ``config.models``
    insertion order), so the "first available" model wins.
    """
    for alias in available_aliases:
        if alias != current_alias:
            return alias
    return None


def build_fallback_llm(
    config: Config,
    alias: str,
    *,
    current_llm: LLM | None,
    session_id: str | None,
    oauth: OAuthManager | None,
) -> LLM | None:
    """Build a fresh :class:`LLM` for ``alias`` mirroring the current thinking.

    Returns ``None`` when the alias is unknown or the provider cannot create
    an LLM (e.g. missing base_url / model).
    """
    from kimi_cli.llm import create_llm

    model_cfg = config.models.get(alias)
    if model_cfg is None:
        logger.warning("Model fallback: unknown alias {alias}", alias=alias)
        return None
    provider = config.providers.get(model_cfg.provider)
    if provider is None:
        logger.warning(
            "Model fallback: provider {provider} for alias {alias} not found",
            provider=model_cfg.provider,
            alias=alias,
        )
        return None

    thinking: bool | None = None
    if current_llm is not None:
        effort = getattr(current_llm.chat_provider, "thinking_effort", None)
        if effort is not None:
            thinking = effort != "off"

    return create_llm(
        provider,
        model_cfg,
        thinking=thinking,
        session_id=session_id,
        oauth=oauth,
    )


def persist_default_model(config: Config, alias: str) -> None:
    """Persist ``alias`` as the ``default_model`` in the config file.

    No-op (with a debug log) when the config has no backing file (inline
    ``--config``) or the alias is already the default. Errors are logged, not
    raised — a failed persist must never break the in-session fallback.
    """
    from kimi_cli.config import load_config, save_config

    if config.source_file is None:
        logger.debug(
            "Model fallback: no config source file; skipping persist of {alias}", alias=alias
        )
        return
    try:
        config_for_save = load_config()
        if config_for_save.default_model == alias:
            return
        config_for_save.default_model = alias
        save_config(config_for_save)
        logger.info("Model fallback: persisted default_model={alias}", alias=alias)
    except Exception as exc:  # noqa: BLE001 - persistence must never break a fallback
        logger.warning("Model fallback: failed to persist default_model: {error}", error=exc)


async def maybe_fallback_on_unavailable(
    *,
    config: Config,
    current_alias: str | None,
    current_llm: LLM | None,
    session_id: str | None,
    oauth: OAuthManager | None,
    timeout: float = MODEL_FALLBACK_CHECK_TIMEOUT_S,
) -> tuple[LLM | None, str | None]:
    """Probe the current provider and, if needed, build a fallback LLM.

    Args:
        config: Loaded configuration.
        current_alias: The alias of the currently selected model (a key in
            ``config.models``). When ``None``, no fallback is attempted.
        current_llm: The active LLM (used to mirror thinking effort).
        session_id / oauth: Forwarded to :func:`build_fallback_llm`.

    Returns:
        ``(fallback_llm, fallback_alias)`` when a switch is required, or
        ``(None, None)`` when the current model is available / unknown / no
        alternative exists. The caller is responsible for installing the
        returned LLM on the runtime and persisting the alias.
    """
    if not current_alias or current_llm is None:
        return None, None
    current_model_cfg = config.models.get(current_alias)
    if current_model_cfg is None:
        return None, None

    provider_key = current_model_cfg.provider
    available = await list_provider_available_aliases(provider_key, config, timeout=timeout)
    if available is None:
        # Unknown availability (network issue / unsupported provider). Keep
        # the current model rather than guessing.
        return None, None
    if current_alias in available:
        # Current model is still served. Nothing to do.
        return None, None

    fallback_alias = pick_fallback_alias(current_alias, available)
    if fallback_alias is None:
        logger.warning(
            "Model fallback: {current} unavailable and no alternative in provider {provider}",
            current=current_alias,
            provider=provider_key,
        )
        return None, None

    logger.warning(
        "Model fallback: {current} unavailable, switching to {fallback}",
        current=current_alias,
        fallback=fallback_alias,
    )
    fallback_llm = build_fallback_llm(
        config,
        fallback_alias,
        current_llm=current_llm,
        session_id=session_id,
        oauth=oauth,
    )
    if fallback_llm is None:
        return None, None
    return fallback_llm, fallback_alias
