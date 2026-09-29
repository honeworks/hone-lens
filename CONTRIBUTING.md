# Contributing to hone-lens

Thanks for helping. This page has the setup, the quality gates and the conventions the code follows. The
design is in [design/](design/README.md): read [design/current.md](design/current.md) before changing
behaviour.

## Setup and the quality gates

```bash
uv sync --all-extras                 # everything, dev tools included (Python 3.11+)
scripts/check.sh                     # all quality gates; "green" means this exits 0
```

Until hone-flow is on PyPI, uv takes the `flow` extra's hone-flow from its GitHub repository (a git source
in `pyproject.toml`). To work on both at once, point that source at your local hone-flow checkout with a
uv `path` source (`{ path = "<checkout>", editable = true }`) and do not commit that change.

Other useful commands:

```bash
uv run pytest                        # default suite: fast, offline, deterministic
uv run pytest tests/e2e              # acceptance cases only
uv run ruff check . && uv run ruff format .
uv run pyright                       # strict for src/
uv build
scripts/gpu-lock.sh uv run pytest -m gpu --timeout=900   # real-model tests (Ollama), see below
```

`scripts/check.sh` runs: `uv sync`, `ruff check`, `ruff format --check`, `pyright`, the default test suite
with coverage >= 90% on `src/`, `uv build`, and a smoke test that installs the wheel alone into a fresh
venv and runs the quickstart.

## Keep it simple

Simple, easy to understand, maintainable code comes first after correctness.

- Build the simplest thing that meets the guaranteed behaviour in
  [design/current.md](design/current.md#10-guaranteed-behaviour-acceptance-cases). Plain functions and small
  dataclasses; composition over inheritance; data over objects with behaviour.
- No speculative generality: add an abstraction only with two real uses today, or where the design names
  an extension point (a port, the `@detector` registry, an entry point, a user-supplied function).
- No layers that only forward calls, no plugin systems, no clever one-liners.
- Limits: cyclomatic complexity <= 10 per function (ruff `C901`), about 40 lines per function, about 300
  lines per module, at most 6 parameters.
- Core dependencies: standard library, pydantic and numpy. A new one needs a design decision saying why
  the standard library is not enough.
- Delete dead code, unused parameters and single-caller helper layers.

## Code conventions

1. **Useful alone.** The core never imports another honeworks package or an optional extra; adapters live
   in `hone_lens.adapters`, one module per extra, imported lazily (`tests/unit/test_import_boundaries.py`).
2. **Ports and adapters.** hone-lens owns its ports (`ports.py`); implementations are injected through
   constructor arguments or entry points; every port has a fake in `hone_lens.testing` and a contract
   checker that the fake passes.
3. **Explicit failure.** Never swallow errors silently; "could not score" is `None`, never `0`; errors
   that cross the public API are `HoneLensError` subclasses with a message that says what to do.
4. **Records.** hone-lens reads spans in the shape described in
   [design/current.md](design/current.md#7-records) and never records secrets.
5. **Determinism.** Explicit seeds; never use the built-in `hash()` for ids (use `hashlib`).
6. **Style.** ruff (line length 110), pyright strict for `src/`, `pathlib.Path`, UTC ISO-8601 times,
   `logging.getLogger("hone_lens")` and no prints in library code.

## Tests

| Suite | Folder | Runs by default | Uses |
|---|---|---|---|
| unit | `tests/unit/` | yes | fakes |
| contract | `tests/contract/` | yes | fakes, fake HTTP transports |
| integration | `tests/integration/` | yes | local resources (SQLite, files, subprocesses); the real hone-flow when installed |
| acceptance | `tests/e2e/` | yes | the public API and CLI, as a user calls them |
| real models | `tests/gpu/` | no (marker `gpu`) | Ollama on your machine |

- Write tests first for public behaviour. Every acceptance case in
  [design/current.md](design/current.md#10-guaranteed-behaviour-acceptance-cases) has a test in
  `tests/e2e/` named `test_ac<N>_<slug>` that fails if the feature is removed.
- The README, every Python block in `docs/` and every file in `examples/` are executed by the test suite.
  A new public concept gets an example with a What / How / Why / Run docstring and a row in
  `examples/README.md`.
- Never weaken a test to make it pass.

### Real-model tests and `scripts/gpu-lock.sh`

The `gpu` suite (AC-15) calls a local Ollama. Models are chosen with `HONE_TEST_OLLAMA_URL`,
`HONE_TEST_TEXT_MODEL` and `HONE_TEST_EMBED_MODEL`; tests skip with a reason when the server or a model is
missing, and unload the models they loaded. `scripts/gpu-lock.sh` runs a command while holding a
machine-wide `flock` (`HONE_GPU_LOCK`, default `/tmp/honeworks-gpu.lock`), so real-model runs of several
honeworks repositories on one GPU never overlap; run the `gpu` suite through it.

## Changing the design

Design changes are written down before they are built:

1. Write `design/changes/NNNN-<short-name>.md` with status `proposed` and the sections Status, Context,
   Problem, Options, Decision, Consequences, and Migration and compatibility.
2. The maintainer reviews it; the status becomes `accepted` (or `rejected`).
3. Implement it from [`design/current.md`](design/current.md) and the accepted change records, tests
   first.
4. Update `design/current.md`, set the record to `implemented in <version>`, and add a `CHANGELOG.md`
   entry that links to it.

Smaller implementation choices that need no change record go into
[`design/decisions.md`](design/decisions.md).

## Pull requests

- One branch per change, named `<type>/<short-name>` after the Conventional Commit types (`feat/ftp-storage`).
- Fill in [`.github/pull_request_template.md`](.github/pull_request_template.md): what, why (issue and
  change record), how it was tested, which docs changed, and the end of the `scripts/check.sh` output.
- claude[bot] reviews every pull request, with inline comments and suggested changes
  ([`.github/workflows/claude-review.yml`](.github/workflows/claude-review.yml)); on a pull request from a
  fork, the maintainer starts it with a `@claude review` comment. Answer each thread: agree and fix,
  disagree with a reason, or ask. A thread is resolved when it is fixed or decided.
- `main` accepts changes only through pull requests, with CI green and every review thread resolved.
- The code owners in [`.github/CODEOWNERS`](.github/CODEOWNERS) are asked to review automatically.
- The maintainer merges.

## Commits

Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `build:`, `ci:`), subject
<= 72 characters, a body that explains why, one logical change per commit, `scripts/check.sh` green before
each commit. Commits written with an AI tool end with a `Co-Authored-By:` line naming it. Never
commit secrets, `.hone/`, model weights or large binaries.

Contributors using AI coding tools will find a short brief for them in [`AGENTS.md`](AGENTS.md); Claude
Code users also get the whole workflow as skills, reviewer agents and hooks in [`.claude/`](.claude/).
