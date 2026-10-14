import pytest

from kimi_cli.tools.re._util import run_command


@pytest.mark.asyncio
async def test_run_command_captures_output_and_exit_code():
    code, output = await run_command("printf", "hello\nworld")
    assert code == 0
    assert "hello" in output and "world" in output


@pytest.mark.asyncio
async def test_run_command_reports_nonzero_exit():
    code, output = await run_command("false")
    assert code != 0
