Disassemble a binary using capstone. Auto-detects architecture and endianness from the ELF
header; a `.text`-relative virtual address or a raw file offset selects the start point.
Emits each instruction as `address  bytes  mnemonic operands`. Use after BinIdent to confirm
the format, and BinStrings for embedded data.
