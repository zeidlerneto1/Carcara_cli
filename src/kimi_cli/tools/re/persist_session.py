from pathlib import Path
from typing import override

from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.soul.agent import Runtime
from kimi_cli.tools.utils import ToolResultBuilder, load_desc


class Params(BaseModel):
    target: str = Field(description="The binary or library name (may keep .so/.a/.dll).")
    notes: str = Field(description="The complete state markdown to persist.")
    merge: bool = Field(
        default=True, description="Merge with an existing file, preserving its tools section."
    )


class PersistSession(CallableTool2[Params]):
    name: str = "PersistSession"
    description: str = load_desc(Path(__file__).parent / "persist_session.md", {})
    params: type[Params] = Params

    def __init__(self, runtime: Runtime) -> None:
        super().__init__()
        self._runtime = runtime

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)
        if _is_unsafe_target(params.target):
            return builder.error(
                f"Invalid target `{params.target}`: plain file name, no separators or `..`.",
                brief=f"Invalid target `{params.target}`",
            )

        rev_dir = self._runtime.session.work_dir / "rev"
        await rev_dir.mkdir(parents=True, exist_ok=True)
        rev_file = rev_dir / f"{params.target}.md"

        content = params.notes
        if params.merge and await rev_file.exists():
            existing = await rev_file.read_text()
            tools_section = _extract_tools_section(existing)
            if tools_section:
                content = f"{tools_section}\n{params.notes}"

        await rev_file.write_text(content)
        builder.write(str(rev_file))
        return builder.ok(f"Persisted analysis to {rev_file}.")


def _is_unsafe_target(target: str) -> bool:
    return "/" in target or "\\" in target or ".." in target


def _extract_tools_section(text: str) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == "## Ferramentas":
            start = idx
            break
    else:
        return ""
    end = len(lines)
    for jdx in range(start + 1, len(lines)):
        if lines[jdx].startswith("## "):
            end = jdx
            break
    return "\n".join(lines[start:end]).strip()
