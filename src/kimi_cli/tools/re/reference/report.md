# RE Report Structure

Structure findings so they are reproducible and reviewable. Use the `PersistSession` tool to checkpoint state into `rev/<target>.md` as you go, then assemble the final report from it.

## 1. Header
- **Target**: exact path/hash (sha256) of the sample.
- **Scope**: what was analyzed (which functions, which protocol surfaces).
- **Date / analyst**.
- **Authorized?** state the authorization for testing (see `legal`).

## 2. Identification
- Architecture, endianness, stripped status, linked libraries, entry point, imports (from `BinIdent`).
- Toolchain hints (compiler version strings, `__libc_start_main` signatures).

## 3. Analysis state
- Checklist of what is done vs pending (function map, identified sinks, reached code paths). Keep it honest — pending items are useful, not a failure.

## 4. Map (VA ↔ offset)
- Table mapping virtual addresses to file offsets, per function, with status (identified / partially analyzed / unresolved). This is the retomada contract — a future session must be able to pick up from here.

## 5. Findings
- For each finding: location (VA + function), a one-line summary, the primitive (see `memory`), severity, and evidence (disassembly snippet, trigger input).
- Separate confirmed exploitable bugs from suspicions.

## 6. Pending / next steps
- Functions still to map, primitives to chain, a PoC to build, a fuzzing campaign to run.

## 7. Conclusion
- One paragraph: what the target does, whether a realistic exploit path exists, and the highest-value next move.

## Writing guidance
- Prefer concrete addresses and inputs over vague prose; the report is a working document, not marketing.
- Never include real credentials, keys, or user-identifying data in the report (sanitize to placeholders).
- Keep the retomada sections (`Map`, `Estado da análise`, `Pendências`) machine-parseable so the `reverser` agent can resume cleanly.
