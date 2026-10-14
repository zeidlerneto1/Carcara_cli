# Exploitation Techniques by Architecture

## ROP (Return-Oriented Programming)
- With NX enabled, reuse existing code: chain gadgets ending in `ret` to build arbitrary logic without injecting code.
- Build a ROP chain on the stack (or via a stack pivot to heap) that calls functions like `system`/`execve` or `mprotect`.
- Needs a gadget set: find with `ROPgadget --binary`, `ropper`, or Ghidra/capstone on the target.

## ret2libc
- Simplest ROP: overwrite `ret` with the address of `system`, set up `rdi` to point to `/bin/sh` (x86-64) or stack-arg (x86-32), return to it.
- Requires a libc leak (or a non-ASLR libc) to know `system`/`/bin/sh` offsets.

## ret2csu
- On x86-64 with no easy `pop rdi; ret` gadget, use the `__libc_csu_init` gadget to control `rbx`, `rbp`, `r12`–`r15` and call an arbitrary function with registers set as args.
- The classic fallback when a PIE/non-PIE binary lacks convenient gadgets.

## SROP (Sigreturn-Oriented Programming)
- A `syscall; ret` gadget plus a crafted `sigreturn` frame lets you set all registers at once and invoke `execve` (or another syscall) without a large ROP chain.
- Powerful when a `syscall` gadget exists and you control the stack.

## SEH Overflow (Windows)
- On 32-bit Windows, an exception-handler overwrite: overflow past the buffer to replace the SEH record's handler pointer with a `pop pop ret` gadget, then point to your shellcode.
- Requires the binary to be compiled without `/GS` canary or with a bypassable one; modern Windows adds CFG/SEHOP (see `mitigations`).

## VirtualProtect (Windows)
- Instead of pushing shellcode and jumping, ROP into `VirtualProtect` to make the stack/heap executable, then jump to your shellcode.
- The Windows analogue of `mprotect` on Unix; bypasses DEP/NX.

## ARM Thumb
- Thumb mode uses 16-bit instructions; gadgets and addresses differ from ARM mode. A `bx`/`blx` with the low bit set switches to Thumb.
- On MIPS (common in the target firmware), note delay-slot semantics: the instruction after a branch/jump always executes — account for it when placing a jump target (the `Disassemble` tool is delay-slot aware).
- ROP on MIPS is harder (no `ret`-style pop-chain density); prefer `jalr`-based chains or heap/format-string primitives.
