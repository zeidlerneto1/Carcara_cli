from __future__ import annotations

import os
from typing import Any, NamedTuple, cast

import aiohttp
import httpx
from pydantic import BaseModel

from kimi_cli.auth import KIMI_CODE_PLATFORM_ID
from kimi_cli.config import Config, LLMModel, load_config, save_config
from kimi_cli.llm import ModelCapability
from kimi_cli.utils.aiohttp import new_client_session
from kimi_cli.utils.logging import logger


class ModelInfo(BaseModel):
    """Model information returned from the API."""

    id: str
    context_length: int
    supports_reasoning: bool
    supports_image_in: bool
    supports_video_in: bool
    display_name: str | None = None

    @property
    def capabilities(self) -> set[ModelCapability]:
        """Derive capabilities from model info."""
        caps: set[ModelCapability] = set()
        if self.supports_reasoning:
            caps.add("thinking")
        # Models with "thinking" in name are always-thinking
        if "thinking" in self.id.lower():
            caps.update(("thinking", "always_thinking"))
        if self.supports_image_in:
            caps.add("image_in")
        if self.supports_video_in:
            caps.add("video_in")
        if self.id.lower().startswith("kimi-k2"):
            caps.update(("thinking", "image_in", "video_in"))
        return caps


class Platform(NamedTuple):
    id: str
    name: str
    base_url: str
    search_url: str | None = None
    fetch_url: str | None = None
    allowed_prefixes: list[str] | None = None


def _kimi_code_base_url() -> str:
    if base_url := os.getenv("KIMI_CODE_BASE_URL"):
        return base_url
    return "https://api.kimi.com/coding/v1"


PLATFORMS: list[Platform] = [
    Platform(
        id=KIMI_CODE_PLATFORM_ID,
        name="Kimi Code",
        base_url=_kimi_code_base_url(),
        search_url=f"{_kimi_code_base_url()}/search",
        fetch_url=f"{_kimi_code_base_url()}/fetch",
    ),
    Platform(
        id="moonshot-cn",
        name="Moonshot AI Open Platform (moonshot.cn)",
        base_url="https://api.moonshot.cn/v1",
        allowed_prefixes=["kimi-k"],
    ),
    Platform(
        id="moonshot-ai",
        name="Moonshot AI Open Platform (moonshot.ai)",
        base_url="https://api.moonshot.ai/v1",
        allowed_prefixes=["kimi-k"],
    ),
]

_PLATFORM_BY_ID = {platform.id: platform for platform in PLATFORMS}
_PLATFORM_BY_NAME = {platform.name: platform for platform in PLATFORMS}


def get_platform_by_id(platform_id: str) -> Platform | None:
    return _PLATFORM_BY_ID.get(platform_id)


def get_platform_by_name(name: str) -> Platform | None:
    return _PLATFORM_BY_NAME.get(name)


MANAGED_PROVIDER_PREFIX = "managed:"


def managed_provider_key(platform_id: str) -> str:
    return f"{MANAGED_PROVIDER_PREFIX}{platform_id}"


def managed_model_key(platform_id: str, model_id: str) -> str:
    return f"{platform_id}/{model_id}"


def parse_managed_provider_key(provider_key: str) -> str | None:
    if not provider_key.startswith(MANAGED_PROVIDER_PREFIX):
        return None
    return provider_key.removeprefix(MANAGED_PROVIDER_PREFIX)


def is_managed_provider_key(provider_key: str) -> bool:
    return provider_key.startswith(MANAGED_PROVIDER_PREFIX)


def get_platform_name_for_provider(provider_key: str) -> str | None:
    platform_id = parse_managed_provider_key(provider_key)
    if not platform_id:
        return None
    platform = get_platform_by_id(platform_id)
    return platform.name if platform else None


def _select_retry_api_keys(
    *,
    attempted_api_key: str,
    resolved_api_key: str,
    fallback_api_key: str,
) -> list[str]:
    result: list[str] = []
    for candidate in (resolved_api_key, fallback_api_key):
        if not candidate or candidate == attempted_api_key or candidate in result:
            continue
        result.append(candidate)
    return result


async def refresh_managed_models(config: Config) -> bool:
    if not config.is_from_default_location:
        return False

    managed_providers = {
        key: provider for key, provider in config.providers.items() if is_managed_provider_key(key)
    }
    if not managed_providers:
        return False

    changed = False
    updates: list[tuple[str, str, list[ModelInfo]]] = []
    oauth_manager = None
    for provider_key, provider in managed_providers.items():
        platform_id = parse_managed_provider_key(provider_key)
        if not platform_id:
            continue
        platform = get_platform_by_id(platform_id)
        if platform is None:
            logger.warning("Managed platform not found: {platform}", platform=platform_id)
            continue

        fallback_api_key = provider.api_key.get_secret_value()
        api_key = fallback_api_key
        if provider.oauth:
            if oauth_manager is None:
                from kimi_cli.auth.oauth import OAuthManager

                oauth_manager = OAuthManager(config)
            try:
                await oauth_manager.ensure_fresh()
            except Exception as exc:
                logger.warning(
                    "Failed to refresh OAuth token before model sync for {platform}: {error}",
                    platform=platform_id,
                    error=exc,
                )
            api_key = oauth_manager.resolve_api_key(provider.api_key, provider.oauth)
        if not api_key:
            logger.warning(
                "Missing API key for managed provider: {provider}",
                provider=provider_key,
            )
            continue
        try:
            models = await list_models(platform, api_key)
        except aiohttp.ClientResponseError as exc:
            if exc.status != 401 or provider.oauth is None or oauth_manager is None:
                logger.error(
                    "Failed to refresh models for {platform}: {error}",
                    platform=platform_id,
                    error=exc,
                )
                continue
            logger.warning(
                "Received 401 while refreshing models for {platform}; attempting token refresh",
                platform=platform_id,
            )
            refresh_exc: Exception | None = None
            try:
                await oauth_manager.ensure_fresh(force=True)
            except Exception as exc2:
                refresh_exc = exc2
                logger.warning(
                    "Failed to refresh OAuth token after 401 for {platform}: {error}",
                    platform=platform_id,
                    error=exc2,
                )

            retry_api_keys = _select_retry_api_keys(
                attempted_api_key=api_key,
                resolved_api_key=oauth_manager.resolve_api_key(provider.api_key, provider.oauth),
                fallback_api_key=fallback_api_key,
            )
            if not retry_api_keys:
                logger.error(
                    "Failed to refresh models for {platform}: {error}",
                    platform=platform_id,
                    error=refresh_exc or exc,
                )
                continue
            retry_exc: Exception | None = None
            for retry_api_key in retry_api_keys:
                try:
                    models = await list_models(platform, retry_api_key)
                    break
                except Exception as exc3:
                    retry_exc = exc3
            else:
                logger.error(
                    "Failed to refresh models for {platform}: {error}",
                    platform=platform_id,
                    error=retry_exc or refresh_exc or exc,
                )
                continue
        except Exception as exc:
            logger.error(
                "Failed to refresh models for {platform}: {error}",
                platform=platform_id,
                error=exc,
            )
            continue

        updates.append((provider_key, platform_id, models))
        if _apply_models(config, provider_key, platform_id, models):
            changed = True

    if changed:
        config_for_save = load_config()
        save_changed = False
        for provider_key, platform_id, models in updates:
            if _apply_models(config_for_save, provider_key, platform_id, models):
                save_changed = True
        if save_changed:
            save_config(config_for_save)
    return changed


async def list_models(platform: Platform, api_key: str) -> list[ModelInfo]:
    async with new_client_session() as session:
        models = await _list_models(
            session,
            base_url=platform.base_url,
            api_key=api_key,
        )
    if platform.allowed_prefixes is None:
        return models
    prefixes = tuple(platform.allowed_prefixes)
    return [model for model in models if model.id.startswith(prefixes)]


async def _list_models(
    session: aiohttp.ClientSession,
    *,
    base_url: str,
    api_key: str,
) -> list[ModelInfo]:
    models_url = f"{base_url.rstrip('/')}/models"
    try:
        async with session.get(
            models_url,
            headers={"Authorization": f"Bearer {api_key}"},
            raise_for_status=True,
        ) as response:
            resp_json = await response.json()
    except aiohttp.ClientError:
        raise

    data = resp_json.get("data")
    if not isinstance(data, list):
        raise ValueError(f"Unexpected models response for {base_url}")

    result: list[ModelInfo] = []
    for item in cast(list[dict[str, Any]], data):
        model_id = item.get("id")
        if not model_id:
            continue
        raw_display_name = item.get("display_name")
        display_name = str(raw_display_name) if raw_display_name else None
        result.append(
            ModelInfo(
                id=str(model_id),
                context_length=int(item.get("context_length") or 0),
                supports_reasoning=bool(item.get("supports_reasoning")),
                supports_image_in=bool(item.get("supports_image_in")),
                supports_video_in=bool(item.get("supports_video_in")),
                display_name=display_name,
            )
        )
    return result


def _apply_models(
    config: Config,
    provider_key: str,
    platform_id: str,
    models: list[ModelInfo],
) -> bool:
    changed = False
    model_keys: list[str] = []

    for model in models:
        model_key = managed_model_key(platform_id, model.id)
        model_keys.append(model_key)

        existing = config.models.get(model_key)
        capabilities = model.capabilities or None  # empty set -> None

        if existing is None:
            config.models[model_key] = LLMModel(
                provider=provider_key,
                model=model.id,
                max_context_size=model.context_length,
                capabilities=capabilities,
                display_name=model.display_name,
            )
            changed = True
            continue

        if existing.provider != provider_key:
            existing.provider = provider_key
            changed = True
        if existing.model != model.id:
            existing.model = model.id
            changed = True
        if existing.max_context_size != model.context_length:
            existing.max_context_size = model.context_length
            changed = True
        if existing.capabilities != capabilities:
            existing.capabilities = capabilities
            changed = True
        if existing.display_name != model.display_name:
            existing.display_name = model.display_name
            changed = True

    removed_default = False
    model_keys_set = set(model_keys)
    for key, model in list(config.models.items()):
        if model.provider != provider_key:
            continue
        if key in model_keys_set:
            continue
        del config.models[key]
        if config.default_model == key:
            removed_default = True
        changed = True

    if removed_default:
        if model_keys:
            config.default_model = model_keys[0]
        else:
            config.default_model = next(iter(config.models), "")
        changed = True

    if config.default_model and config.default_model not in config.models:
        config.default_model = next(iter(config.models), "")
        changed = True

    return changed


def _carcara_browser_headers(api_key: str | None = None) -> dict[str, str]:
    """Return the browser-style headers the Carcara endpoint expects.

    Without these the server responds with 403 Forbidden. The defaults mirror the
    headers already used by CarcaraProvider (packages/kosong)."""
    from kosong.contrib.chat_provider.carcara import CarcaraProvider

    headers = dict(CarcaraProvider.DEFAULT_HEADERS)
    headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


async def list_carcara_models(
    base_url: str,
    api_key: str | None = None,
) -> list[ModelInfo]:
    """List models available on a Carcara (llama.cpp-compatible) endpoint.

    The Carcara /models response uses a schema different from the standard
    supports_* flags: capabilities live in models[].capabilities (e.g.
    ["completion", "multimodal"]) and each model also appears in data[]
    with id/context_length/max_input_tokens/max_output_tokens.
    """
    models_url = f"{base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(
            headers=_carcara_browser_headers(api_key),
            timeout=httpx.Timeout(30.0, connect=10.0),
        ) as client:
            response = await client.get(models_url)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Carcara models request failed with HTTP {status}: {url}",
            status=exc.response.status_code,
            url=models_url,
        )
        return []
    except httpx.HTTPError as exc:
        logger.warning("Carcara models request failed: {error}", error=exc)
        return []

    data = payload.get("data")
    if not isinstance(data, list):
        logger.warning("Unexpected Carcara models response for {url}", url=models_url)
        return []

    # Capabilities come from the models array keyed by id.
    caps_by_id: dict[str, set[str]] = {}
    models_array = payload.get("models")
    if isinstance(models_array, list):
        for item in cast(list[dict[str, Any]], models_array):
            model_id = item.get("id") or item.get("model")
            if not model_id:
                continue
            raw_caps = item.get("capabilities")
            if isinstance(raw_caps, list):
                caps_by_id[str(model_id)] = {str(c) for c in raw_caps}

    result: list[ModelInfo] = []
    for item in cast(list[dict[str, Any]], data):
        model_id = item.get("id")
        if not model_id:
            continue
        model_id = str(model_id)
        caps = caps_by_id.get(model_id, set())
        result.append(
            ModelInfo(
                id=model_id,
                context_length=int(item.get("context_length") or 0),
                supports_reasoning="reasoning" in caps or "thinking" in caps,
                supports_image_in="multimodal" in caps or "image_in" in caps,
                supports_video_in="multimodal" in caps or "video_in" in caps,
                display_name=item.get("name") or item.get("display_name"),
            )
        )
    return result


def _apply_carcara_models(
    config: Config,
    provider_key: str,
    models: list[ModelInfo],
) -> bool:
    """Update Carcara model entries in config.models in place.

    Unlike the managed flow, Carcara model keys are hand-written aliases (e.g.
    carcara/deepseek) that do not match the endpoint model id. We therefore
    update existing entries in place (matched by their model name) and add any
    newly discovered endpoint models, instead of deleting/re-keying them.

    For existing entries only the context length and display name are refreshed;
    user-set capabilities are preserved. Newly added models pick up capabilities
    from the endpoint."""
    changed = False
    by_id = {model.id: model for model in models}

    for model_cfg in list(config.models.values()):
        if model_cfg.provider != provider_key:
            continue
        info = by_id.get(model_cfg.model)
        if info is None:
            continue
        # Update context/display only. Preserve user-set capabilities (e.g. the
        # endpoint may omit reasoning for a model the user configured with it).
        if model_cfg.max_context_size != info.context_length:
            model_cfg.max_context_size = info.context_length
            changed = True
        if model_cfg.display_name != info.display_name:
            model_cfg.display_name = info.display_name
            changed = True

    existing_ids = {
        model_cfg.model
        for model_cfg in config.models.values()
        if model_cfg.provider == provider_key
    }
    for info in models:
        if info.id in existing_ids:
            continue
        key = f"{provider_key}/{info.id.lower()}"
        config.models[key] = LLMModel(
            provider=provider_key,
            model=info.id,
            max_context_size=info.context_length,
            capabilities=info.capabilities or None,
            display_name=info.display_name,
        )
        changed = True

    if config.default_model and config.default_model not in config.models:
        config.default_model = next(iter(config.models), "")
        changed = True

    return changed


async def refresh_carcara_models(config: Config) -> bool:
    """Refresh models for all carcara-typed providers from their endpoint."""
    carcara_providers = {
        key: provider for key, provider in config.providers.items() if provider.type == "carcara"
    }
    if not carcara_providers:
        return False

    updates: list[tuple[str, list[ModelInfo]]] = []
    for provider_key, provider in carcara_providers.items():
        if not provider.base_url:
            logger.warning("Carcara provider missing base_url: {provider}", provider=provider_key)
            continue
        api_key = provider.api_key.get_secret_value() if provider.api_key else None
        models = await list_carcara_models(provider.base_url, api_key)
        updates.append((provider_key, models))

    if not updates:
        return False

    # Apply against a freshly loaded copy and persist only on real changes.
    changed = False
    config_for_save = load_config()
    for provider_key, models in updates:
        if _apply_carcara_models(config_for_save, provider_key, models):
            changed = True
    if changed:
        save_config(config_for_save)
    return changed
