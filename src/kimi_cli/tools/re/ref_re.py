from __future__ import annotations

from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools.utils import ToolResultBuilder, load_desc

REFERENCE_DIR = Path(__file__).parent / "reference"
TOPICS = [
    "mitigations",
    "bypass",
    "memory",
    "techniques",
    "fuzzing",
    "report",
    "checklists",
    "legal",
]


class Params(BaseModel):
    topic: str = Field(description="The reference topic to read (e.g. 'mitigations').")


class RefRE(CallableTool2[Params]):
    name: str = "RefRE"
    description: str = load_desc(Path(__file__).parent / "ref_re.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)
        path = REFERENCE_DIR / f"{params.topic}.md"
        if not path.is_file():
            builder.write(
                f"Unknown reference topic `{params.topic}`.\nValid topics: {', '.join(TOPICS)}."
            )
            return builder.error(
                f"Unknown reference topic `{params.topic}`. Valid topics: {', '.join(TOPICS)}.",
                brief=f"Unknown topic `{params.topic}`",
            )
        builder.write(path.read_text(encoding="utf-8").rstrip())
        return builder.ok(f"Loaded reference topic: {params.topic}.")
