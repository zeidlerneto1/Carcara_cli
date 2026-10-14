import pytest

from kimi_cli.agentspec import REVERSER_AGENT_FILE, load_agent_spec
from kimi_cli.soul.agent import Runtime, load_agent

RE_TOOLS = {"BinIdent", "BinStrings", "Disassemble", "RefRE", "PersistSession"}


@pytest.mark.asyncio
async def test_reverser_agent_loads_re_toolset(runtime: Runtime):
    agent = await load_agent(REVERSER_AGENT_FILE, runtime, mcp_configs=[])
    assert agent.name == "reverser"
    tool_names = {tool.name for tool in agent.toolset.tools}
    assert tool_names >= RE_TOOLS, f"missing: {RE_TOOLS - tool_names}"


def test_reverser_agent_inherits_default_subagents():
    spec = load_agent_spec(REVERSER_AGENT_FILE)
    assert set(spec.subagents) == {"coder", "explore", "plan", "reverser"}
