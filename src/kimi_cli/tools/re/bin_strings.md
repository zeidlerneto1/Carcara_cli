Extract printable strings from a binary file using the `strings` command. Supports
choosing the minimum string length (`-n`) and the encoding (`-e`: s = 7-bit ASCII,
b = 8-bit bytes, S = 16-bit little-endian, l = 16-bit big-endian). Useful for
surfacing embedded literals such as error messages, symbols, or hardcoded data.
