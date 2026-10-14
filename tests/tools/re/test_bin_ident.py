import pytest

from kimi_cli.tools.re.bin_ident import BinIdent, Params


@pytest.mark.asyncio
async def test_bin_ident_recognizes_mips_elf(mips_elf_path: str):
    result = await BinIdent()(Params(target=mips_elf_path))
    assert not result.is_error
    assert "MIPS" in result.output
    assert "MSB" in result.output  # big-endian from `file`


@pytest.mark.asyncio
async def test_bin_ident_missing_file_returns_error(tmp_path):
    result = await BinIdent()(Params(target=str(tmp_path / "nope")))
    assert result.is_error
