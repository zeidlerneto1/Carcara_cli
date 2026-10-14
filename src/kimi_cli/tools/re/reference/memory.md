# Memory Corruption Primitives

## Stack Buffer Overflow
- Writing past the end of a stack buffer overwrites saved registers and the return address.
- Classic control flow: overflow → overwrite `ret` → ROP / ret2libc. See `techniques`.
- Blocked by canary (leak or avoid) and NX (ROP). See `mitigations`.

## Heap / glibc
- glibc allocator (`malloc`/`free`) keeps freed chunks in bins. The classic primitives:
- **tcache poisoning**: after a double-free or UAF, overwrite a tcache `next` pointer to make the next `malloc` return an arbitrary address (write-what-where). Same-`size_t`-class, no size check on tcache chunks — the easiest modern path.
- **fastbin dup**: double-free the same fastbin chunk twice so two allocations alias; corrupt metadata to allocate over a target.
- **unsorted bin**: freed large chunk lands in the unsorted bin; its `bk` pointer can be abused (unsorted bin attack) to write a large value to `global_max_fast`, enabling further corruption.
- **House of Force**: corrupt the top chunk size so `malloc` returns memory below the heap, letting a large allocation reach an arbitrary target (e.g. the GOT or libc).
- **House of Spirit**: trick `free` into treating a fake chunk on the stack as a heap chunk, then reallocate it to write over stack data.
- **FSOP** (_File Structure Oriented Programming_): corrupt a `FILE*` (`_IO_FILE`) vtable / `_IO_2_1_stdout_` so a subsequent I/O call leaks or writes to an arbitrary target. Often the endgame when only the heap is corruptable.

## Use-After-Free (UAF)
- A pointer is used after its object is freed. Reallocate a same-size object (or a fake chunk) at the freed slot, then the dangling pointer reads/writes attacker-controlled memory.
- Pair with fastbin/tcache dup to build an arbitrary write.

## Format String
- `printf(user_input)` with no format → `%n` writes the number of bytes printed so far to an address pulled from the stack/register.
- Use positional params (`%N$p`) to walk the stack: leak stack/libc, and `%N$n` to write (e.g. patch a GOT entry or a pointer).
- Mitigated by `_FORTIFY_SOURCE`/`-Wformat-security`, but common in legacy firmware.

## Integer Overflow / Truncation
- A size or index arithmetic that wraps (e.g. `bufsize - 1` when `bufsize == 0`), leading to a smaller allocation than the copy or an out-of-bounds index.
- Often the root cause enabling a subsequent heap or stack overflow. Check arithmetic types and signedness.

## TOCTOU (Time-of-Check to Time-of-Use)
- A file/object is validated, then used later assuming the same state — a race swaps it in between.
- Common in firmware with privileged file handling; often leads to arbitrary file read/write without touching memory.
