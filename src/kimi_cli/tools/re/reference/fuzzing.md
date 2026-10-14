# Fuzzing

Use fuzzing to find memory-corruption bugs in the target before or during manual analysis.

## Tooling
- **AFL++**: coverage-guided, forked-mode fuzzer. Instrument with `afl-clang-fast` or `afl-gcc`, feed a corpus in `in/`, let it find crashes in `out/`. Great for file parsers in firmware.
- **libFuzzer**: in-process coverage-guided fuzzer built into clang. Best when you can compile a harness around a parser function.

## Minimize crashes
- `afl-tmin -i crash -o min crash.bin` shrinks a crashing input to the smallest input that still triggers the crash — essential for triage and for finding the exact corruption site.
- For libFuzzer, the tool auto-minimizes when a crash is found and writes a reproducer file.

## Corpus quality
- Start with real samples extracted from the firmware (config files, `upnpd` request bodies, protocol payloads).
- Use `afl-cmin` to remove redundant corpus entries and speed up the campaign.
- Add dictionary files (`-x dict.txt`) for structured formats (HTTP headers, XML, UPnP SOAP) to reach deep parser states.

## Triage by CWE
- Classify each crash before assuming exploitability:
  - **CWE-121 / CWE-122**: stack / heap buffer overflow — likely exploitable; check canary & NX (see `mitigations`).
  - **CWE-416**: use-after-free — check heap state and whether a same-size reallocation reaches the freed pointer.
  - **CWE-415**: double free — often leads to tcache/fastbin dup (see `memory`).
  - **CWE-190**: integer overflow — see if it drives a later allocation/copy size.
  - **CWE-134**: format string — direct arbitrary read/write if reachable with user input.
- Reproduce under a debugger (gdb with `catch signal SIGSEGV` / `catch throw`) and confirm the crash is deterministic before spending exploit time.

## Sanitizers
- Build with ASan/UBSan to catch OOB reads/writes and signed-overflow even when they would not crash natively. ASan often reports the exact line of the overflow.

## When fuzzing is not enough
- Coverage-guided fuzzing misses logic bugs (auth bypass, TOCTOU, race conditions) and multi-step protocol states. Pair it with manual disassembly/decompilation (see `Disassemble`, `GhidraDecompile`).
