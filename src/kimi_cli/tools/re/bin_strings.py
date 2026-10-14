from pathlib import Path
from typing import Literal, override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools.re._util import run_command
from kimi_cli.tools.utils import ToolResultBuilder, load_desc


class Params(BaseModel):
    target: str = Field(description="The path to the binary to scan for strings.")
    min_length: int = Field(default=4, description="The minimum string length to report.")
    encoding: Literal["s", "b", "S", "l"] = Field(
        default="s",
        description=(
            "The character encoding: s=7-bit ASCII, b=8-bit bytes, "
            "S=16-bit little-endian, l=16-bit big-endian."
        ),
    )


class BinStrings(CallableTool2[Params]):
    name: str = "BinStrings"
    description: str = load_desc(Path(__file__).parent / "bin_strings.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)

        cmd = ("strings", "-n", str(params.min_length), "-e", params.encoding, params.target)
        code, output = await run_command(*cmd)
        if code != 0:
            return builder.error(
                f"`strings` failed on `{params.target}` (exit {code}).",
                brief=output.strip() or f"strings failed with exit {code}",
            )
        builder.write(output)

        return builder.ok(f"Extracted strings from {params.target}.")
