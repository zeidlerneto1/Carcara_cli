"""Tests for the automatic model-fallback helpers.

Covers:
- pick_fallback_alias ordering / no-alternative cases
- build_fallback_llm mirroring the current thinking effort
- persist_default_model no-op when there is no source file
- maybe_fallback_on_unavailable: current available / unavailable / unknown / no alt
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kosong.chat_provider import APIStatusError
from kosong.tooling.simple import SimpleToolset
from pydantic import SecretStr

from kimi_cli.config import Config, LLMModel, LLMProvider
from kimi_cli.llm import LLM
from kimi_cli.soul import model_fallback as mf
from kimi_cli.soul.agent import Agent, Runtime
from kimi_cli.soul.context import Context
from kimi_cli.soul.kimisoul import KimiSoul


def _make_config(models: dict[str, LLMModel], providers: dict[str, LLMProvider]) -> Config:
    return Config(default_model="a", models=models, providers=providers)


class TestPickFallbackAlias:
    def test_returns_first_different(self):
        assert mf.pick_fallback_alias("a", ["a", "b", "c"]) == "b"

    def test_current_not_in_list(self):
        # Current model is absent from the available list; the first available wins.
        assert mf.pick_fallback_alias("a", ["b", "c"]) == "b"

    def test_no_alternative(self):
        assert mf.pick_fallback_alias("a", ["a"]) is None

    def test_empty_available(self):
        assert mf.pick_fallback_alias("a", []) is None


class TestBuildFallbackLLM:
    def test_builds_llm_with_thinking(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
                "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        current_llm = MagicMock()
        current_llm.chat_provider.thinking_effort = "high"

        with patch("kimi_cli.llm.create_llm", return_value="LLM-B") as mock_create:
            result = mf.build_fallback_llm(
                config,
                "b",
                current_llm=current_llm,
                session_id="s1",
                oauth=None,
            )
        assert result == "LLM-B"
        # thinking mirrored from current effort "high" -> True
        _, kwargs = mock_create.call_args
        assert kwargs["thinking"] is True
        assert kwargs["session_id"] == "s1"

    def test_unknown_alias(self):
        config = _make_config(
            models={"a": LLMModel(provider="p", model="model-a", max_context_size=1024)},
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        assert (
            mf.build_fallback_llm(config, "zzz", current_llm=None, session_id=None, oauth=None)
            is None
        )


class TestPersistDefaultModel:
    def test_no_source_file_is_noop(self):
        config = _make_config(
            models={"a": LLMModel(provider="p", model="model-a", max_context_size=1024)},
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        config.source_file = None
        # Should not raise even without a backing file.
        mf.persist_default_model(config, "a")

    def test_persists_when_changed(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
                "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        config.source_file = Path("/tmp/nonexistent-config.toml")

        # persist_default_model imports load_config/save_config from
        # kimi_cli.config inside the function, so patch them there.
        with (
            patch(
                "kimi_cli.config.load_config",
                return_value=Config(
                    default_model="a",
                    models=config.models,
                    providers=config.providers,
                ),
            ),
            patch("kimi_cli.config.save_config") as mock_save,
        ):
            mf.persist_default_model(config, "b")
        assert mock_save.called
        saved = mock_save.call_args[0][0]
        assert saved.default_model == "b"

    def test_no_change_when_already_default(self):
        config = _make_config(
            models={"a": LLMModel(provider="p", model="model-a", max_context_size=1024)},
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        config.source_file = Path("/tmp/nonexistent-config.toml")
        with (
            patch(
                "kimi_cli.config.load_config",
                return_value=Config(
                    default_model="a", models=config.models, providers=config.providers
                ),
            ),
            patch("kimi_cli.config.save_config") as mock_save,
        ):
            mf.persist_default_model(config, "a")
        assert not mock_save.called


class TestMaybeFallbackOnUnavailable:
    @pytest.mark.asyncio
    async def test_current_available_no_change(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
                "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        current_llm = MagicMock()
        current_llm.model_config = config.models["a"]
        current_llm.chat_provider.thinking_effort = None

        with patch.object(
            mf, "list_provider_available_aliases", new=AsyncMock(return_value=["a", "b"])
        ):
            llm, alias = await mf.maybe_fallback_on_unavailable(
                config=config,
                current_alias="a",
                current_llm=current_llm,
                session_id="s",
                oauth=None,
            )
        assert llm is None and alias is None

    @pytest.mark.asyncio
    async def test_current_unavailable_switches(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
                "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        current_llm = MagicMock()
        current_llm.model_config = config.models["a"]
        current_llm.chat_provider.thinking_effort = None

        with (
            patch.object(mf, "list_provider_available_aliases", new=AsyncMock(return_value=["b"])),
            patch.object(mf, "build_fallback_llm", return_value="LLM-B") as mock_build,
        ):
            llm, alias = await mf.maybe_fallback_on_unavailable(
                config=config,
                current_alias="a",
                current_llm=current_llm,
                session_id="s",
                oauth=None,
            )
        assert alias == "b"
        assert llm == "LLM-B"
        mock_build.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_availability_no_change(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        current_llm = MagicMock()
        current_llm.model_config = config.models["a"]
        current_llm.chat_provider.thinking_effort = None

        with patch.object(mf, "list_provider_available_aliases", new=AsyncMock(return_value=None)):
            llm, alias = await mf.maybe_fallback_on_unavailable(
                config=config,
                current_alias="a",
                current_llm=current_llm,
                session_id="s",
                oauth=None,
            )
        assert llm is None and alias is None

    @pytest.mark.asyncio
    async def test_no_alternative_no_change(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        current_llm = MagicMock()
        current_llm.model_config = config.models["a"]
        current_llm.chat_provider.thinking_effort = None

        with patch.object(mf, "list_provider_available_aliases", new=AsyncMock(return_value=[])):
            llm, alias = await mf.maybe_fallback_on_unavailable(
                config=config,
                current_alias="a",
                current_llm=current_llm,
                session_id="s",
                oauth=None,
            )
        assert llm is None and alias is None

    @pytest.mark.asyncio
    async def test_none_current_alias_noop(self):
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        llm, alias = await mf.maybe_fallback_on_unavailable(
            config=config,
            current_alias=None,
            current_llm=None,
            session_id="s",
            oauth=None,
        )
        assert llm is None and alias is None


# ─────────────────────────────────────────────────────────────────────────────
# KimiSoul._try_model_fallback integration tests
# ─────────────────────────────────────────────────────────────────────────────
def _soul_from_runtime(runtime: Runtime, llm: LLM, tmp_path: Path) -> KimiSoul:
    rt = Runtime(
        config=runtime.config,
        llm=llm,
        session=runtime.session,
        builtin_args=runtime.builtin_args,
        denwa_renji=runtime.denwa_renji,
        approval=runtime.approval,
        labor_market=runtime.labor_market,
        environment=runtime.environment,
        notifications=runtime.notifications,
        background_tasks=runtime.background_tasks,
        skills=runtime.skills,
        oauth=runtime.oauth,
        additional_dirs=runtime.additional_dirs,
        skills_dirs=runtime.skills_dirs,
        role=runtime.role,
    )
    agent = Agent(
        name="Fallback Test Agent",
        system_prompt="Fallback test prompt.",
        toolset=SimpleToolset(),
        runtime=rt,
    )
    context = Context(file_backend=tmp_path / "history.jsonl")
    return KimiSoul(agent, context=context)


def _make_llm(model_id: str, ctx: int = 1024) -> LLM:
    from kosong.chat_provider.mock import MockChatProvider

    return LLM(
        chat_provider=MockChatProvider([]),
        max_context_size=ctx,
        capabilities=set(),
        model_config=LLMModel(provider="p", model=model_id, max_context_size=ctx),
        provider_config=LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr("")),
    )


@pytest.mark.asyncio
async def test_try_model_fallback_switches_llm(runtime: Runtime, tmp_path: Path) -> None:
    # Config with two models of the same provider.
    config = runtime.config
    model_a = LLMModel(provider="p", model="model-a", max_context_size=1024)
    model_b = LLMModel(provider="p", model="model-b", max_context_size=2048)
    config.models = {"a": model_a, "b": model_b}
    config.default_model = "a"
    # llm.model_config must be the SAME object as in config.models so the
    # alias lookup by identity succeeds.
    current = _make_llm("model-a")
    current.model_config = model_a
    soul = _soul_from_runtime(runtime, current, tmp_path)

    exc = APIStatusError(404, "Model not found: model-a")
    fallback_llm = _make_llm("model-b")
    with (
        patch(
            "kimi_cli.soul.model_fallback.maybe_fallback_on_unavailable",
            new=AsyncMock(return_value=(fallback_llm, "b")),
        ),
        patch("kimi_cli.soul.model_fallback.persist_default_model") as mock_persist,
        patch.object(soul, "_emit_model_fallback_notice") as mock_notice,
    ):
        switched = await soul._try_model_fallback(exc)

    assert switched is True
    assert soul._model_fallback_attempted is True
    assert soul._runtime.llm is fallback_llm
    assert config.default_model == "b"
    assert mock_persist.called
    assert mock_notice.called


@pytest.mark.asyncio
async def test_try_model_fallback_not_404(runtime: Runtime, tmp_path: Path) -> None:
    config = runtime.config
    config.models = {
        "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
    }
    current = _make_llm("model-a")
    soul = _soul_from_runtime(runtime, current, tmp_path)

    exc = APIStatusError(500, "Internal server error")
    switched = await soul._try_model_fallback(exc)
    assert switched is False
    assert getattr(soul, "_model_fallback_attempted", False) is False


@pytest.mark.asyncio
async def test_try_model_fallback_once_per_turn(runtime: Runtime, tmp_path: Path) -> None:
    config = runtime.config
    config.models = {
        "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
        "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
    }
    current = _make_llm("model-a")
    soul = _soul_from_runtime(runtime, current, tmp_path)
    soul._model_fallback_attempted = True

    exc = APIStatusError(404, "Model not found: model-a")
    with patch(
        "kimi_cli.soul.model_fallback.maybe_fallback_on_unavailable",
        new=AsyncMock(return_value=("LLM-B", "b")),
    ) as mock_maybe:
        switched = await soul._try_model_fallback(exc)
    assert switched is False
    mock_maybe.assert_not_called()


@pytest.mark.asyncio
async def test_try_model_fallback_no_alternative(runtime: Runtime, tmp_path: Path) -> None:
    config = runtime.config
    config.models = {
        "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
    }
    config.default_model = "a"
    current = _make_llm("model-a")
    soul = _soul_from_runtime(runtime, current, tmp_path)

    exc = APIStatusError(404, "Model not found: model-a")
    with patch(
        "kimi_cli.soul.model_fallback.maybe_fallback_on_unavailable",
        new=AsyncMock(return_value=(None, None)),
    ):
        switched = await soul._try_model_fallback(exc)
    assert switched is False
    assert getattr(soul, "_model_fallback_attempted", False) is False


class TestAvailabilityCache:
    """Tests for the on-disk model availability cache."""

    def test_cache_key_stable(self):
        """The cache key is deterministic and different for different providers."""
        k1 = mf._cache_key("p1", "http://a", "key1")
        k2 = mf._cache_key("p1", "http://a", "key1")
        k3 = mf._cache_key("p2", "http://a", "key1")
        assert k1 == k2
        assert k1 != k3

    def test_cache_roundtrip(self, tmp_path):
        """Writing then reading returns the same model ids."""
        key = "testkey123"
        with patch.object(mf, "_cache_path", return_value=tmp_path / "cache.json"):
            mf._write_cache(key, {"model-a", "model-b"})
            result = mf._read_cache(key)
        assert result == {"model-a", "model-b"}

    def test_cache_miss_on_wrong_key(self, tmp_path):
        """Reading with a different key returns None."""
        with patch.object(mf, "_cache_path", return_value=tmp_path / "cache.json"):
            mf._write_cache("keyA", {"model-a"})
            assert mf._read_cache("keyB") is None

    def test_cache_miss_when_expired(self, tmp_path):
        """Reading an expired cache entry returns None."""
        key = "testkey123"
        with patch.object(mf, "_cache_path", return_value=tmp_path / "cache.json"):
            mf._write_cache(key, {"model-a"})
            # Manually backdate the timestamp to force expiry.
            import json as _json

            path = tmp_path / "cache.json"
            data = _json.loads(path.read_text(encoding="utf-8"))
            data["ts"] = data["ts"] - mf.MODEL_FALLBACK_CACHE_TTL_S - 10
            path.write_text(_json.dumps(data), encoding="utf-8")
            assert mf._read_cache(key) is None

    def test_cache_missing_file(self, tmp_path):
        """Reading a nonexistent cache file returns None."""
        with patch.object(mf, "_cache_path", return_value=tmp_path / "nonexistent.json"):
            assert mf._read_cache("anykey") is None

    def test_cache_corrupt_file(self, tmp_path):
        """Reading a corrupt cache file returns None rather than raising."""
        path = tmp_path / "cache.json"
        path.write_text("not json at all", encoding="utf-8")
        with patch.object(mf, "_cache_path", return_value=path):
            assert mf._read_cache("anykey") is None

    @pytest.mark.asyncio
    async def test_list_provider_available_aliases_uses_cache(self, tmp_path):
        """When the cache is fresh, no network probe happens."""
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
                "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        key = mf._cache_key("p", "http://x", "")
        with patch.object(mf, "_cache_path", return_value=tmp_path / "cache.json"):
            mf._write_cache(key, {"model-a", "model-b"})
            with patch.object(mf, "_list_provider_model_ids", new=AsyncMock()) as mock_probe:
                result = await mf.list_provider_available_aliases("p", config)
        assert result == ["a", "b"]
        mock_probe.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_provider_available_aliases_writes_cache(self, tmp_path):
        """After a successful network probe, the cache is populated."""
        config = _make_config(
            models={
                "a": LLMModel(provider="p", model="model-a", max_context_size=1024),
                "b": LLMModel(provider="p", model="model-b", max_context_size=2048),
            },
            providers={
                "p": LLMProvider(type="carcara", base_url="http://x", api_key=SecretStr(""))
            },
        )
        key = mf._cache_key("p", "http://x", "")
        with patch.object(mf, "_cache_path", return_value=tmp_path / "cache.json"):
            with patch.object(
                mf, "_list_provider_model_ids", new=AsyncMock(return_value={"model-a", "model-b"})
            ):
                result = await mf.list_provider_available_aliases("p", config)
            # Cache should now be populated.
            assert mf._read_cache(key) == {"model-a", "model-b"}
        assert result == ["a", "b"]
