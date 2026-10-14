Persist the current reverse-engineering state for a target to `rev/<target>.md` in the working directory.

Provide `target` (the binary/library name, may keep `.so`/`.a`/`.dll`) and `notes` (the complete state markdown). By default (`merge: true`) an existing file keeps its `## Ferramentas` section while the new state is applied; set `merge: false` to overwrite. The written absolute path is returned.
