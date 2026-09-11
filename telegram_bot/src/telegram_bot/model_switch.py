"""In-place model switching for per-chat UserSession instances.

Allows a Telegram user to change the active LLM model of their current
session without recreating the KimiCLI (so the conversation context is
preserved). Mirrors the technique used by KimiSoul._try_model_fallback:
create a fresh LLM from the config and install it on ``runtime.llm``.

Also provides helpers to list available models and to toggle thinking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from kimi_cli.auth.oauth import OAuthManager
from kimi_cli.config import Config
from kimi_cli.llm import create_llm, model_display_name

from telegram_bot.session_manager import UserSession

logger = logging.getLogger(__name__)

# callback_data prefix for model-switch callbacks (must not collide with
# "apr:" used by approvals or "q:" used by questions).
PREFIX = "mdl"


def _callback(model_alias: str) -> str:
    return f"{PREFIX}:{model_alias}"


def parse_callback(callback_data: str) -> str | None:
    """Parse a model callback_data into the model alias, or None."""
    if not callback_data.startswith(PREFIX + ":"):
        return None
    _, alias = callback_data.split(":", 1)
    return alias or None


@dataclass
class ModelInfo:
    """A selectable model from the config, with display + capability flags."""

    alias: str
    display: str
    supports_thinking: bool
    is_default: bool


def list_models(config: Config) -> list[ModelInfo]:
    """Return all models from the config, ordered, with capability flags."""
    infos: list[ModelInfo] = []
    for alias, m in config.models.items():
        caps = m.capabilities or set()
        infos.append(
            ModelInfo(
                alias=alias,
                display=model_display_name(alias, m) or alias,
                supports_thinking="thinking" in caps or "always_thinking" in caps,
                is_default=(alias == config.default_model),
            )
        )
    return infos


def current_model_alias(user_session: UserSession) -> str | None:
    """Best-effort alias of the model currently active on this session.

    Matches the runtime's LLMModel object against ``config.models`` (same
    technique used by ``KimiSoul._try_model_fallback``), falling back to the
    config default.
    """
    runtime = getattr(user_session.cli, "_runtime", None)
    if runtime is None:
        return None
    llm = runtime.llm
    if llm is None or llm.model_config is None:
        return None
    config = runtime.config
    for alias, m in config.models.items():
        if m is llm.model_config:
            return alias
    return config.default_model or None


def _supports_capability(user_session: UserSession, capability: str) -> bool:
    """True if the session's active model declares ``capability`` in its set."""
    runtime = getattr(user_session.cli, "_runtime", None)
    if runtime is None:
        return False
    llm = runtime.llm
    if llm is None:
        return False
    caps = getattr(llm, "capabilities", None) or set()
    return capability in caps


def _supports_thinking(user_session: UserSession) -> bool:
    runtime = getattr(user_session.cli, "_runtime", None)
    if runtime is None:
        return False
    llm = runtime.llm
    if llm is None:
        return False
    caps = getattr(llm, "capabilities", None) or set()
    return "thinking" in caps or "always_thinking" in caps


def current_thinking_on(user_session: UserSession) -> bool:
    """Whether thinking is currently active on the session's LLM."""
    if not _supports_thinking(user_session):
        return False
    llm = getattr(getattr(user_session.cli, "_runtime", None), "llm", None)
    if llm is None:
        return False
    effort = getattr(llm.chat_provider, "thinking_effort", None)
    if effort is None:
        return False
    return effort != "off"


async def switch_model(
    user_session: UserSession,
    model_alias: str,
    *,
    thinking: bool | None = None,
) -> tuple[str, bool]:
    """Switch the active LLM on ``user_session`` to ``model_alias``.

    The session context (conversation history) is preserved; only the LLM
    object on the runtime is replaced. Mirrors ``KimiSoul._try_model_fallback``.

    Args:
        user_session: The per-chat UserSession to mutate.
        model_alias: Alias key into ``config.models``.
        thinking: Optional desired thinking state ("on"/"off"/None=keep).

    Returns:
        (display_name, thinking_now_on)

    Raises:
        ValueError: if the alias is not in the config or the LLM could not be
            created.
    """
    runtime = getattr(user_session.cli, "_runtime", None)
    if runtime is None:
        raise ValueError("No runtime attached to this session.")

    config: Config = runtime.config
    if model_alias not in config.models:
        raise ValueError(f"Model '{model_alias}' not found in config.")

    model = config.models[model_alias]
    provider = config.providers[model.provider]

    # Resolve thinking: keep current if None, otherwise force.
    if thinking is None:
        if "always_thinking" in (model.capabilities or set()):
            thinking = True
        elif "thinking" in (model.capabilities or set()):
            # keep the session's current preference
            thinking = current_thinking_on(user_session)
        else:
            thinking = False

    oauth = OAuthManager(config)
    new_llm = create_llm(
        provider,
        model,
        thinking=thinking,
        session_id=runtime.session.id,
        oauth=oauth,
    )
    if new_llm is None:
        raise ValueError(f"Could not create LLM for model '{model_alias}'.")

    # Install on the runtime — next agent step picks it up.
    runtime.llm = new_llm
    # Remember the new alias as the default for this runtime (mirrors fallback).
    config.default_model = model_alias

    display = model_display_name(model_alias, model)
    thinking_now = current_thinking_on(user_session)
    logger.info(
        "Switched model for session to %s (thinking=%s)", display, thinking_now
    )
    return display, thinking_now


def build_model_keyboard(
    config: Config,
    current_alias: str | None,
) -> InlineKeyboardMarkup:
    """Inline keyboard listing every model with a check mark on the current one."""
    rows: list[list[InlineKeyboardButton]] = []
    for info in list_models(config):
        label = ("✅ " if info.alias == current_alias else "") + info.display
        if not info.supports_thinking:
            label += " (sem thinking)"
        rows.append([InlineKeyboardButton(label, callback_data=_callback(info.alias))])
    return InlineKeyboardMarkup(rows)


def build_status_text(
    config: Config,
    user_session: UserSession,
    *,
    yolo: bool,
) -> str:
    """Human-readable status block for /status."""
    alias = current_model_alias(user_session)
    display = alias or "(desconhecido)"
    if alias is not None and alias in config.models:
        m = config.models[alias]
        display = model_display_name(alias, m) or alias
    thinking = current_thinking_on(user_session)
    supports = _supports_thinking(user_session)
    thinking_str = "🧠 on" if thinking else ("🚫 off" if supports else "— (sem suporte)")
    yolo_str = "🟢 yolo (auto-aprova)" if yolo else "🔴 manual (aprova cada ação)"
    default_alias = config.default_model or "(nenhum)"
    return (
        "📊 <b>Status</b>\n\n"
        f"Modelo: <b>{display}</b>\n"
        f"Pensamento: {thinking_str}\n"
        f"Aprovações: {yolo_str}\n"
        f"Padrão no config: {default_alias}"
    )


__all__ = [
    "PREFIX",
    "ModelInfo",
    "_supports_capability",
    "build_model_keyboard",
    "build_status_text",
    "current_model_alias",
    "current_thinking_on",
    "list_models",
    "parse_callback",
    "switch_model",
]
