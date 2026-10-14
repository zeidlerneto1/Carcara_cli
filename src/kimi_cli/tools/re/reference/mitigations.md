# Mitigations

Modern binaries ship with a set of OS- and compiler-provided protections. Identify which are active on a target with `checksec` before planning an exploitation strategy.

## ASLR / KASLR
- ASLR randomizes the base address of the stack, heap, mmap regions, shared libraries, and (on some platforms) the executable.
- KASLR extends the same idea to the kernel image base.
- Check: `cat /proc/sys/kernel/randomize_va_space` (2 = full).
- Consequence: absolute addresses of stack/heap/libc are unknown at run time; leaks become mandatory for most attacks.

## NX / DEP
- Non-Executable stack/heap: the CPU refuses to execute code located in data pages (W^X).
- Check: `readelf -l` — look for an executable stack segment (`GNU_STACK` with `RWE`).
- Consequence: direct shellcode on the stack/heap fails; use ROP/ret2libc instead.

## Stack Canary
- A random value placed between the stack frame and saved registers; corrupted if the buffer overflows into it. Checked before `ret`.
- Check: `checksec --file=...` reports `canary found`.
- Consequence: stack BOF must either leak the canary or avoid reaching the check (e.g. overwrite a function pointer before the canary, or use a format-string leak first).

## RELRO
- `Full RELRO`: GOT is read-only after relocation; `Partial RELRO` leaves `.got.plt` writable.
- Check: `checksec` reports `RELRO: Full` or `Partial`.
- Consequence: full RELRO blocks direct GOT overwrite; partial RELRO leaves the GOT writable for ret2got techniques.

## PIE
- Position-Independent Executable: the main binary is loaded at a random base (subject to ASLR).
- Non-PIE binaries load at a fixed base (often `0x400000` on x86-64), so code addresses are static — no leak needed for the binary itself.
- Check: `readelf -h` — `Type: DYN` (PIE) vs `EXEC` (non-PIE).

## CFG / CET
- Control-Flow Guard / Control-flow Enforcement Technology: indirect branch targets are validated, so hijacking a function pointer or return address to an arbitrary gadget is blocked.
- Check: `checksec` reports `CFG` (Windows) or `CET` (shadow stack / IBT on modern Intel).
- Consequence: classic ROP and vtable hijack are harder; prefer logic bugs or JOP where indirect targets are reachable.

## Using checksec
- `checksec --file=./target` prints canary, NX, PIE, RELRO, fortify, and more.
- Combine with `readelf -h -l` and `file` for a full picture (see the `BinIdent` tool).
