"""Unit tests for the in-place model switching logic."""

from __future__ import annotations

from types import SimpleNamespace

from kimi_cli.config import Config, LLMModel, LLMProvider
from pydantic import SecretStr

from telegram_bot.model_switch import (
    ModelInfo,
    build_model_keyboard,
    build_status_text,
    current_model_alias,
    current_thinking_on,
    list_models,
    parse_callback,
    switch_model,
)


def _make_config() -> Config:
    return Config(
        default_model="p/alpha",
        models={
            "p/alpha": LLMModel(
                provider="p",
                model="alpha-model",
                max_context_size=32_000,
                capabilities={"thinking"},
            ),
            "p/beta": LLMModel(
                provider="p",
                model="beta-model",
                max_context_size=16_000,
                capabilities=set(),
            ),
        },
        providers={
            "p": LLMProvider(
                type="kimi",
                base_url="https://api.test",
                api_key=SecretStr("k"),
            )
        },
    )


def _make_user_session(config: Config, alias: str):
    """Build a fake UserSession with a runtime holding the given model."""
    model = config.models[alias]
    llm = SimpleNamespace(
        model_config=model,
        provider_config=config.providers[model.provider],
        capabilities=model.capabilities or set(),
        max_context_size=model.max_context_size,
        chat_provider=SimpleNamespace(thinking_effort=None, model_name=model.model),
    )
    runtime = SimpleNamespace(config=config, llm=llm, session=SimpleNamespace(id="s1"))
    cli = SimpleNamespace(_runtime=runtime)
    return SimpleNamespace(cli=cli, session=SimpleNamespace(id="s1"))


def test_parse_callback():
    assert parse_callback("mdl:p/alpha") == "p/alpha"
    assert parse_callback("apr:xxx:approve") is None
    assert parse_callback("") is None


def test_list_models_flags_capabilities():
    infos = list_models(_make_config())
    by_alias = {i.alias: i for i in infos}
    assert by_alias["p/alpha"].supports_thinking is True
    assert by_alias["p/beta"].supports_thinking is False
    assert by_alias["p/alpha"].is_default is True
    assert by_alias["p/beta"].is_default is False
    assert isinstance(infos[0], ModelInfo)


def test_current_model_alias():
    config = _make_config()
    user = _make_user_session(config, "p/alpha")
    assert current_model_alias(user) == "p/alpha"
    user2 = _make_user_session(config, "p/beta")
    assert current_model_alias(user2) == "p/beta"


def test_current_thinking_on_reflects_effort():
    config = _make_config()
    user = _make_user_session(config, "p/alpha")
    # default: no effort set -> off
    assert current_thinking_on(user) is False
    # set effort high -> on
    user.cli._runtime.llm.chat_provider.thinking_effort = "high"
    assert current_thinking_on(user) is True
    # set effort off -> off
    user.cli._runtime.llm.chat_provider.thinking_effort = "off"
    assert current_thinking_on(user) is False


def test_build_model_keyboard_marks_current():
    config = _make_config()
    kb = build_model_keyboard(config, "p/alpha")
    # keyboard has one row per model
    assert len(kb.inline_keyboard) == 2
    # find the row for p/alpha and check the label has the check mark
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any("✅" in t for t in labels)


def test_build_status_text_mentions_model():
    config = _make_config()
    user = _make_user_session(config, "p/alpha")
    text = build_status_text(config, user, yolo=False)
    assert "Modelo" in text
    assert "p/alpha" in text or "alpha" in text
    assert "🔴" in text  # manual approval


def test_switch_model_invalid_alias_raises():
    import asyncio
    import pytest

    config = _make_config()
    user = _make_user_session(config, "p/alpha")
    with pytest.raises(ValueError, match="not found in config"):
        asyncio.run(switch_model(user, "does/not-exist"))


def test_switch_model_updates_runtime_llm():
    import asyncio

    import telegram_bot.model_switch as ms

    config = _make_config()
    user = _make_user_session(config, "p/alpha")

    sentinel = SimpleNamespace(
        model_config=config.models["p/beta"],
        provider_config=config.providers["p"],
        capabilities=set(),
        max_context_size=16_000,
        chat_provider=SimpleNamespace(thinking_effort=None, model_name="beta-model"),
    )
    orig = ms.create_llm
    ms.create_llm = lambda *a, **k: sentinel
    try:
        display, thinking = asyncio.run(ms.switch_model(user, "p/beta"))
    finally:
        ms.create_llm = orig

    assert display == "p/beta"
    assert thinking is False
    # runtime llm replaced
    assert user.cli._runtime.llm is sentinel
    # config default updated
    assert config.default_model == "p/beta"
