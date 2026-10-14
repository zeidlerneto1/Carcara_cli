import pytest

from kimi_cli.tools.re.bin_strings import BinStrings, Params


@pytest.mark.asyncio
async def test_bin_strings_finds_embedded_string(mips_elf_path: str):
    result = await BinStrings()(Params(target=mips_elf_path, min_length=4))
    assert not result.is_error
    assert "HELLORE" in result.output


@pytest.mark.asyncio
async def test_bin_strings_missing_file_returns_error(tmp_path):
    result = await BinStrings()(Params(target=str(tmp_path / "nope")))
    assert result.is_error
