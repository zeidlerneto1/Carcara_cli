import struct

import pytest

# Minimal valid ELF32 big-endian MIPS executable.
# Layout:
#   0x000  ELF header (52 bytes)
#   0x034  one PT_LOAD program header (32 bytes)
#   0x054  .text data: nop, jr $ra, addiu $a0,$zero,1, then "HELLORE\0"
#   0x068  .shstrtab data: "\0.text\0.shstrtab\0"
#   0x07c  three section headers (NULL, .text, .shstrtab)

_TEXT_OFFSET = 0x54
_TEXT_SIZE = 12 + 8  # 3 instructions + "HELLORE\0"
SHSTRTAB_OFFSET = _TEXT_OFFSET + _TEXT_SIZE
SHSTRTAB = b"\0.text\0.shstrtab\0"
SHSTRTAB_SIZE = len(SHSTRTAB)
SHOFF = (SHSTRTAB_OFFSET + SHSTRTAB_SIZE + 3) & ~3  # align section headers to 4
FILE_SIZE = SHOFF + 3 * 40  # 3 section headers, each 40 bytes

_CODE = b"\x00\x00\x00\x00" + b"\x03\xe0\x00\x08" + b"\x24\x04\x00\x01" + b"HELLORE\0"


def _build_elf() -> bytes:
    ehdr = struct.pack(
        ">16sHHIIIIIHHHHHH",
        b"\x7fELF" + bytes([1, 2, 1, 0]) + b"\0" * 7,  # e_ident
        2,  # e_type = ET_EXEC
        8,  # e_machine = EM_MIPS
        1,  # e_version
        0x400054,  # e_entry (start of .text)
        0x34,  # e_phoff
        SHOFF,  # e_shoff
        0x1000,  # e_flags (EF_MIPS_ABI_O32-ish, harmless)
        52,  # e_ehsize
        32,  # e_phentsize
        1,  # e_phnum
        40,  # e_shentsize
        3,  # e_shnum
        2,  # e_shstrndx
    )

    phdr = struct.pack(
        ">IIIIIIII",
        1,  # p_type = PT_LOAD
        0,  # p_offset
        0x400000,  # p_vaddr
        0x400000,  # p_paddr
        FILE_SIZE,  # p_filesz
        FILE_SIZE,  # p_memsz
        5,  # p_flags = PF_R | PF_X
        0x1000,  # p_align
    )

    shdr_text = struct.pack(
        ">IIIIIIIIII",
        1,  # sh_name -> ".text"
        1,  # sh_type = SHT_PROGBITS
        6,  # sh_flags = SHF_ALLOC | SHF_EXECINSTR
        0x400054,  # sh_addr
        _TEXT_OFFSET,  # sh_offset
        _TEXT_SIZE,  # sh_size
        0,  # sh_link
        0,  # sh_info
        4,  # sh_addralign
        0,  # sh_entsize
    )
    shdr_shstrtab = struct.pack(
        ">IIIIIIIIII",
        7,  # sh_name -> ".shstrtab"
        3,  # sh_type = SHT_STRTAB
        0,  # sh_flags
        0,  # sh_addr
        SHSTRTAB_OFFSET,  # sh_offset
        SHSTRTAB_SIZE,  # sh_size
        0,  # sh_link
        0,  # sh_info
        1,  # sh_addralign
        0,  # sh_entsize
    )

    shdrs = b"\0" * 40 + shdr_text + shdr_shstrtab  # section 0 = NULL

    return (
        ehdr + phdr + _CODE + SHSTRTAB + b"\0" * (SHOFF - (SHSTRTAB_OFFSET + SHSTRTAB_SIZE)) + shdrs
    )


MIPS_ELF = _build_elf()


@pytest.fixture
def mips_elf_path(tmp_path) -> str:
    path = tmp_path / "hello_re"
    path.write_bytes(MIPS_ELF)
    return str(path)
