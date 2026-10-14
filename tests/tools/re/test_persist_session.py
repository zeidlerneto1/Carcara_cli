import pytest
from kaos.path import KaosPath

from kimi_cli.tools.re.persist_session import Params, PersistSession


@pytest.mark.asyncio
async def test_persist_session_creates_rev_file(runtime, temp_work_dir: KaosPath):
    tool = PersistSession(runtime)
    result = await tool(Params(target="libixml.so", notes="# RE — libixml.so\n\n## Descobertas\nfirst"))
    path = temp_work_dir / "rev" / "libixml.so.md"
    assert not result.is_error
    assert str(path) in result.output
    assert (await path.read_text()).find("first") != -1


@pytest.mark.asyncio
async def test_persist_session_merge_preserves_tools(runtime, temp_work_dir: KaosPath):
    tool = PersistSession(runtime)
    await tool(Params(target="t", notes="# RE — t\n\n## Ferramentas\n| readelf | /usr/bin/readelf |\n\n## Descobertas\nfirst"))
    await tool(Params(target="t", notes="## Descobertas\nsecond"))
    path = temp_work_dir / "rev" / "t.md"
    text = await path.read_text()
    assert "| readelf | /usr/bin/readelf |" in text
    assert "second" in text


@pytest.mark.asyncio
async def test_persist_session_merge_false_overwrites(runtime, temp_work_dir: KaosPath):
    tool = PersistSession(runtime)
    await tool(Params(target="t", notes="## Ferramentas\n| old | x |"))
    await tool(Params(target="t", notes="## Descobertas\nnew", merge=False))
    text = await (temp_work_dir / "rev" / "t.md").read_text()
    assert "| old | x |" not in text
    assert "new" in text


@pytest.mark.asyncio
async def test_persist_session_rejects_path_traversal(runtime):
    tool = PersistSession(runtime)
    result = await tool(Params(target="../../etc/passwd", notes="x"))
    assert result.is_error
