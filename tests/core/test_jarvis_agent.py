from __future__ import annotations

import pytest

from kimi_cli.agentspec import JARVIS_AGENT_FILE
from kimi_cli.soul.agent import Runtime, load_agent

ALL_TOOLS = {
    "Agent",
    "SendDMail",
    "Think",
    "AskUserQuestion",
    "SetTodoList",
    "Shell",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "ReadFile",
    "ReadMediaFile",
    "Glob",
    "Grep",
    "WriteFile",
    "StrReplaceFile",
    "SearchWeb",
    "FetchURL",
    "ExitPlanMode",
    "EnterPlanMode",
}


@pytest.mark.asyncio
async def test_jarvis_agent_uses_all_tools(runtime: Runtime):
    agent = await load_agent(JARVIS_AGENT_FILE, runtime, mcp_configs=[])
    assert agent.name == "jarvis"
    tool_names = {tool.name for tool in agent.toolset.tools}
    assert tool_names == ALL_TOOLS, f"missing: {ALL_TOOLS - tool_names}"


@pytest.mark.asyncio
async def test_jarvis_agent_has_subagents(runtime: Runtime):
    from kimi_cli.agentspec import load_agent_spec

    spec = load_agent_spec(JARVIS_AGENT_FILE)
    assert set(spec.subagents) == {"coder", "explore", "plan"}
