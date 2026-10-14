# Checklists

## Offensive checklist (analyzing a target for bugs)
- [ ] Confirm written authorization / scope for the target (see `legal`).
- [ ] Identify the binary: arch, endianness, stripped, libs, entry, imports (`BinIdent`).
- [ ] Check mitigations: `checksec` — canary, NX, PIE, RELRO, CFG/CET (`mitigations`).
- [ ] Extract strings for hints: creds, format strings, `system`/`exec` calls, file paths (`BinStrings`).
- [ ] Map functions and entry points; find sinks (strcpy/strcat/memcpy/printf/snprintf/system/socket/recv).
- [ ] Trace attacker-controlled input (network payload, config file, HTTP body) to each sink.
- [ ] Identify a primitive: stack BOF, heap, UAF, format string, integer overflow, TOCTOU (`memory`).
- [ ] Decide the bypass path per the decision tree (`bypass`).
- [ ] Build a PoC that deterministically crashes under a debugger; minimize it.
- [ ] Chain to code execution or a meaningful write; or prove exploitation is not feasible and say why.
- [ ] Write the report (`report`) and checkpoint state (`PersistSession`).

## Defensive checklist (hardening a firmware binary)
- [ ] Rebuild with `-fstack-protector-all` (canary), `-fPIE -pie`, `-Wl,-z,relro,-z,now` (full RELRO), `-Wl,-z,noexecstack` (NX).
- [ ] Enable `_FORTIFY_SOURCE=2` and `-Wformat-security`; fix all format-string sinks.
- [ ] Replace `strcpy`/`sprintf`/`gets` with bounded variants; validate all sizes against signed overflow.
- [ ] Drop or sandbox privileged helpers to reduce TOCTOU / file-write surface.
- [ ] Run an ASan/UBSan build through the fuzzing campaign (`fuzzing`) and fix every finding.
- [ ] Remove debug symbols, version banners, and hardcoded credentials before shipping.
- [ ] Re-run `checksec` on the shipped artifact and diff against the baseline.
