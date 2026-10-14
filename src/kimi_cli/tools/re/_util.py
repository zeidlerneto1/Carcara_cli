from __future__ import annotations

import asyncio

import kaos

from kimi_cli.utils.subprocess_env import get_noninteractive_env


async def run_command(*args: str, timeout: int = 120) -> tuple[int, str]:
    process = await kaos.exec(*args, env=get_noninteractive_env())

    process.stdin.close()

    async def _read_stream(stream):
        lines = []
        while True:
            line = await stream.readline()
            if line:
                lines.append(line)
            else:
                break
        return b"".join(lines)

    try:
        stdout, stderr = await asyncio.wait_for(
            asyncio.gather(_read_stream(process.stdout), _read_stream(process.stderr)),
            timeout,
        )
        code = await process.wait()
        return code, (stdout + stderr).decode(errors="replace")
    except asyncio.CancelledError:
        await process.kill()
        raise
    except TimeoutError:
        await process.kill()
        raise
