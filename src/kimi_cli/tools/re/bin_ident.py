from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools.re._util import run_command
from kimi_cli.tools.utils import ToolResultBuilder, load_desc


class Params(BaseModel):
    target: str = Field(description="The path to the binary to identify.")


class BinIdent(CallableTool2[Params]):
    name: str = "BinIdent"
    description: str = load_desc(Path(__file__).parent / "bin_ident.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)

        for cmd in (("file", params.target), ("readelf", "-h", "-l", "-S", "-d", params.target)):
            code, output = await run_command(*cmd)
            if code != 0:
                return builder.error(
                    f"`{cmd[0]}` failed on `{params.target}` (exit {code}).",
                    brief=output.strip() or f"{cmd[0]} failed with exit {code}",
                )
            builder.write(output)

        return builder.ok(f"Identified binary: {params.target}.")
