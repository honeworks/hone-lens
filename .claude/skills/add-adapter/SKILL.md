---
name: add-adapter
description: Add an implementation of one of hone-lens's ports, or another optional integration, with its extra, contract test and docs. Use when adding support for a new service, library or data source.
---

# Add an adapter

The ports are in `src/hone_lens/ports.py` (`design/current.md` §6): `RecordSource`, `TextClient`,
`Embedder`, `Replayer`, `StepRerunner`. A new port, or a change to one, is a design change: `plan-change`
first.

1. **Where.** Adapters go in `src/hone_lens/adapters/` (like `src/hone_lens/adapters/openai.py`); record
   formats it reads go in `src/hone_lens/sources/`.
2. **Optional dependency.** Add an extra in `pyproject.toml`; import the third-party library only inside
   the adapter module. The core must still import without it: `tests/unit/test_import_boundaries.py`.
3. **Contract.** Run the checker from `hone_lens.testing.contracts` (`check_record_source`,
   `check_text_client`, `check_embedder`, `check_replayer`, `check_step_rerunner`) against it in
   `tests/contract/`.
4. **Behaviour the port promises.** A source keeps every span id it reads, so findings can point back
   to evidence; a client that can't answer never falls back silently.
5. **Tests with recorded data or HTTP**, never a live service, in the default suite; real calls only in
   `tests/gpu/` (`real-model-tests`).
6. **Entry point** when the port is chosen by name (`hone.text_clients`, `hone.embedders` in
   `pyproject.toml`).
7. `sync-docs`: the install line, `docs/adapters.md` or `docs/sources.md`, an example if it's new.
