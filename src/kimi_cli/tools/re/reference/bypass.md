# Bypass Decision Tree

Choose an exploitation strategy from the mitigations present on the target. Work top-down; stop at the first path whose prerequisites are all satisfied.

## 1. Can you control the instruction pointer at all?
- No reachable memory-corruption primitive (no BOF, no UAF, no format string, no integer overflow) → **stop**. There is nothing to exploit here; look for logic bugs or a different entry point.
- Yes → continue.

## 2. Is there an NX stack?
- **No NX** (executable stack) → push shellcode on the stack, jump to it. Done.
- **NX enabled** → you cannot execute data pages. Continue.

## 3. Is there a stack canary?
- **No canary** → proceed with the overflow directly.
- **Canary present**:
  - Can you **leak** it (format string, OOB read)? Leak, then reproduce it exactly in the overflow.
  - Can you **overwrite a target before the canary** (a pointer, a function pointer, a flag) without crossing the canary? Do that.
  - Otherwise the canary blocks a straight stack overwrite → look for a different primitive.

## 4. Are you doing a return-address overwrite or a forward-edge hijack?
- **Return address** → ROP / ret2libc / SROP.
- **Forward edge** (function pointer, GOT, vtable, `jmp reg`) → GOT overwrite, ret2got, or vtable hijack.
- If **CFG/CET** blocks indirect targets, prefer return-address attacks (shadow stack may also block those → consider logic bugs).

## 5. Are ASLR addresses known?
- **Leak available** → use the leak to build a ret2libc / ret2dl with real addresses.
- **No leak, non-PIE binary** → code is at a fixed base; you still need libc/heap addresses. Use a partial-overwrite or brute-force technique if the target allows retries.
- **No leak, PIE + full ASLR** → need to defeat ASLR: use an info leak, or a one-shot that does not need exact addresses (e.g. `ret2csu`, `ret2dl-resolve`).

## 6. Pick the primitive that fits (see `memory` and `techniques`)
- Stack BOF → ROP, ret2libc, SROP, ret2csu.
- Heap (glibc) → tcache poisoning, fastbin dup, unsorted bin, House of Force/Spirit, FSOP.
- UAF → fastbin dup / tcache dup to forge a fake chunk, then hijack.
- Format string → arbitrary read/write to leak and patch GOT or a pointer.
- Integer overflow / TOCTOU → logic-level primitive that sidesteps memory mitigations entirely.
