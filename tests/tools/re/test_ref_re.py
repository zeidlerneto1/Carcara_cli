import pytest

from kimi_cli.tools.re.ref_re import Params, RefRE

TOPICS = ["mitigations", "bypass", "memory", "techniques", "fuzzing", "report", "checklists", "legal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", TOPICS)
async def test_ref_re_returns_topic(topic: str):
    result = await RefRE()(Params(topic=topic))
    assert not result.is_error
    assert len(result.output.strip()) > 0


@pytest.mark.asyncio
async def test_ref_re_unknown_topic_errors():
    result = await RefRE()(Params(topic="nope"))
    assert result.is_error
    assert "mitigations" in result.output
