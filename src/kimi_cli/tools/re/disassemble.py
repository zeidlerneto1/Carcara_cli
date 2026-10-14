from pathlib import Path
from typing import Literal, override

import capstone  # type: ignore[reportMissingTypeStubs]
from kosong.tooling import CallableTool2, ToolReturnValue
from pydantic import BaseModel, Field

from kimi_cli.tools.re._util import run_command
from kimi_cli.tools.utils import ToolResultBuilder, load_desc


class Params(BaseModel):
    target: str = Field(description="The path to the binary to disassemble.")
    arch: Literal["mips", "arm", "x86"] | None = Field(
        default=None, description="Target architecture. Defaults to auto-detect."
    )
    endian: Literal["big", "little"] | None = Field(
        default=None, description="Byte order. Defaults to auto-detect."
    )
    offset: int | None = Field(
        default=None, description="File offset (in bytes) to disassemble from."
    )
    va: int | None = Field(
        default=None,
        description="Virtual address to disassemble from; translated to a file offset via .text.",
    )
    size: int = Field(default=0x40, description="Number of bytes to disassemble.")
    count: int = Field(default=0, description="Max instructions to emit; 0 means all in size.")


class Disassemble(CallableTool2[Params]):
    name: str = "Disassemble"
    description: str = load_desc(Path(__file__).parent / "disassemble.md", {})
    params: type[Params] = Params

    @override
    async def __call__(self, params: Params) -> ToolReturnValue:
        builder = ToolResultBuilder(max_line_length=None)
        target = params.target

        try:
            header = Path(target).read_bytes()[:20]
        except OSError as exc:
            return builder.error(f"Could not read `{target}`: {exc}", brief=f"cannot read {target}")

        if not header.startswith(b"\x7fELF"):
            return builder.error(
                f"`{target}` is not an ELF binary.", brief=f"{target} is not an ELF binary"
            )

        endian = params.endian
        if endian is None:
            ei_data = header[5]
            if ei_data == 2:
                endian = "big"
            elif ei_data == 1:
                endian = "little"
            else:
                return builder.error(
                    f"Unsupported EI_DATA value {ei_data} in `{target}`.",
                    brief=f"unsupported EI_DATA {ei_data}",
                )

        ei_class = header[4]
        if ei_class == 1:
            mode = capstone.CS_MODE_MIPS32
        elif ei_class == 2:
            mode = capstone.CS_MODE_MIPS64
        else:
            return builder.error(
                f"Unsupported EI_CLASS value {ei_class} in `{target}`.",
                brief=f"unsupported EI_CLASS {ei_class}",
            )

        arch = params.arch or "mips"
        try:
            cs_arch = {
                "mips": capstone.CS_ARCH_MIPS,
                "arm": capstone.CS_ARCH_ARM,
                "x86": capstone.CS_ARCH_X86,
            }[arch]
            cs_mode = mode | (
                capstone.CS_MODE_BIG_ENDIAN if endian == "big" else capstone.CS_MODE_LITTLE_ENDIAN
            )
            md = capstone.Cs(cs_arch, cs_mode)
        except (KeyError, capstone.CsError) as exc:
            return builder.error(f"Could not build capstone disassembler: {exc}", brief=str(exc))

        text = await self._text_section(target)
        if text is None:
            return builder.error(
                f"Could not resolve the .text section of `{target}` via readelf.",
                brief="could not resolve .text",
            )
        text_addr, text_off, text_size = text

        offset = params.offset
        if offset is None and params.va is not None:
            if not text_addr <= params.va < text_addr + text_size:
                return builder.error(
                    f"va {params.va:#x} is not inside .text "
                    f"({text_addr:#x}..{text_addr + text_size:#x}).",
                    brief=f"va {params.va:#x} outside .text",
                )
            offset = text_off + (params.va - text_addr)
        elif offset is None:
            offset = text_off

        try:
            data = Path(target).read_bytes()
        except OSError as exc:
            return builder.error(f"Could not read `{target}`: {exc}", brief=f"cannot read {target}")

        if offset < 0 or offset >= len(data):
            return builder.error(
                f"Offset {offset} is out of range (file is {len(data)} bytes).",
                brief="offset out of range",
            )

        chunk = data[offset : offset + params.size]
        if not chunk:
            return builder.error(
                "No bytes to disassemble at the requested range.", brief="empty range"
            )

        for idx, insn in enumerate(md.disasm(chunk, offset)):  # pyright: ignore[reportUnknownMemberType]
            if params.count and idx >= params.count:
                break
            bytes_hex = insn.bytes.hex()
            builder.write(f"0x{insn.address:x}  {bytes_hex}  {insn.mnemonic} {insn.op_str}\n")

        return builder.ok(f"Disassembled {offset:#x} ({params.size} bytes) of {target}.")

    @staticmethod
    async def _text_section(target: str) -> tuple[int, int, int] | None:
        code, output = await run_command("readelf", "-S", target)
        if code != 0:
            return None
        for line in output.splitlines():
            parts = line.split()
            if ".text" not in parts:
                continue
            # Section header row: [Nr] Name Type Addr Off Size ES Flg Lk Inf Al
            idx = parts.index(".text")
            if len(parts) < idx + 5:
                continue
            addr = int(parts[idx + 2], 16)
            off = int(parts[idx + 3], 16)
            size = int(parts[idx + 4], 16)
            return addr, off, size
        return None
