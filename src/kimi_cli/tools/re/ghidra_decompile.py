from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools import SkipThisTool
from kimi_cli.tools.re._util import run_command
from kimi_cli.tools.utils import ToolResultBuilder, load_desc


class Params(BaseModel):
    target: str = Field(description="The path to the binary to decompile.")
    function: str | None = Field(
        default=None,
        description="The name of the function to decompile. Defaults to all functions.",
    )
    timeout: int = Field(default=600, description="Timeout in seconds for the headless analysis.")


class GhidraDecompile(CallableTool2[Params]):
    name: str = "GhidraDecompile"
    description: str = load_desc(Path(__file__).parent / "ghidra_decompile.md", {})
    params: type[Params] = Params

    def __init__(self) -> None:
        headless = _find_analyze_headless()
        if headless is None:
            raise SkipThisTool()
        super().__init__()
        self._headless = headless

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)

        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = str(Path(tmpdir) / "project")
            project_name = "reproj"
            cmd = [self._headless, project_dir, project_name, "-import", params.target]
            if params.function:
                cmd.extend(["-postScript", "DecompileFunction.java", params.function])
            code, output = await run_command(*cmd, timeout=params.timeout)
            if code != 0:
                return builder.error(
                    f"Ghidra headless analysis failed on `{params.target}` (exit {code}).",
                    brief=output.strip() or f"analyzeHeadless failed with exit {code}",
                )
            builder.write(output)

        return builder.ok(f"Decompiled {params.target} with Ghidra.")


def _find_analyze_headless() -> str | None:
    found = shutil.which("analyzeHeadless")
    if found:
        return found
    snap_root = Path("/snap/ghidra/current")
    if snap_root.is_dir():
        for candidate in snap_root.rglob("support/analyzeHeadless"):
            if candidate.is_file():
                return str(candidate)
    return None
