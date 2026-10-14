import pytest

from kimi_cli.tools.re.disassemble import Disassemble, Params


@pytest.mark.asyncio
async def test_disassemble_mips_auto_detect(mips_elf_path: str):
    result = await Disassemble()(Params(target=mips_elf_path, size=8))
    assert not result.is_error
    assert "nop" in result.output
    assert "jr" in result.output


@pytest.mark.asyncio
async def test_disassemble_non_binary_returns_error(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_text("not an elf at all")
    result = await Disassemble()(Params(target=str(p)))
    assert result.is_error
