import pytest

from kimi_cli.tools import SkipThisTool
from kimi_cli.tools.re.ghidra_decompile import GhidraDecompile, Params


def test_ghidra_decompile_skips_when_headless_absent(monkeypatch):
    monkeypatch.setattr("kimi_cli.tools.re.ghidra_decompile._find_analyze_headless", lambda: None)
    with pytest.raises(SkipThisTool):
        GhidraDecompile()


@pytest.mark.asyncio
async def test_ghidra_decompile_dispatch_path(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr("shutil.which", lambda _: "/fake/analyzeHeadless")

    async def fake_run(*args: str, timeout: int = 120):
        calls.append(list(args))
        return 0, "decompiled code"

    monkeypatch.setattr("kimi_cli.tools.re.ghidra_decompile.run_command", fake_run)
    tool = GhidraDecompile()
    result = await tool(Params(target="/tmp/sample", function="main"))
    assert not result.is_error
    assert "decompiled code" in result.output
    assert calls and calls[0][0].endswith("analyzeHeadless")
