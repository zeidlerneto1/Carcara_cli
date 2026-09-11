from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from kimi_cli.auth.platforms import _normalize_ollama_base_url, list_ollama_models


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:11434", "http://localhost:11434/api"),
        ("http://localhost:11434/", "http://localhost:11434/api"),
        ("http://localhost:11434/api", "http://localhost:11434/api"),
        ("http://localhost:11434/api/", "http://localhost:11434/api"),
        ("http://localhost:11434/v1", "http://localhost:11434/api"),
        ("http://localhost:11434/v1/", "http://localhost:11434/api"),
    ],
)
def test_normalize_ollama_base_url(raw: str, expected: str):
    assert _normalize_ollama_base_url(raw) == expected


@pytest.mark.asyncio
async def test_list_ollama_models_builds_native_tags_url():
    """list_ollama_models must hit /api/tags even when given a /v1 URL."""
    async def fake_get(url, **kwargs):
        assert url == "http://localhost:11434/api/tags"
        return AsyncMock(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {
                "models": [{"name": "qwen3:8b"}, {"name": "llama3.1:8b"}]
            },
        )

    with patch("kimi_cli.auth.platforms.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(return_value=AsyncMock(get=fake_get))
        mock_client.return_value.__aexit__ = AsyncMock(return_value=False)

        infos = await list_ollama_models("http://localhost:11434/v1")

    assert {info.id for info in infos} == {"qwen3:8b", "llama3.1:8b"}
