# AGENTS.md: hone-lens

Notes for contributors who use AI coding tools (Claude Code, Codex, Cursor and others) in this repository.
People and tools follow the same rules: [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is
`hone-lens` (import `hone_lens`) analyzes thousands of AI workflow runs to find issues, their causes and
replay-tested fixes. It is part of the honeworks family of Python packages but works on its own.

Read before changing behaviour:
- [design/current.md](design/current.md): the design as it stands, including the guaranteed behaviour
  (acceptance cases);
- [design/changes/](design/changes/): accepted and proposed design changes;
- [design/decisions.md](design/decisions.md): smaller implementation choices.

## Commands
```bash
uv sync --all-extras                 # install everything (dev included)
scripts/check.sh                     # all quality gates: lint, format, types, tests + coverage, build, wheel smoke test
uv run pytest                        # default suite (fast, offline, deterministic)
uv run pytest tests/e2e              # acceptance cases only
scripts/gpu-lock.sh uv run pytest -m gpu --timeout=900   # real-model tests, under the machine-wide GPU lock
uv run ruff check . && uv run ruff format .
uv run pyright                       # types (strict for src/)
```

## Layout
```
src/hone_lens/         package (public API in __init__.py with an explicit __all__)
  workspace.py          the Workspace API
  sources/              record sources (hone:, otlp:, phoenix:, langfuse:)
  detectors/  outputs/  describe.py  coding/  cause.py  replay.py    the analysis stages
  findings.py  report/  the findings model and reports
  ports.py              Protocols this package owns
  adapters/             optional integrations (openai, flow), imported lazily, one module per extra
  testing/              public fakes for every port, contract checkers, synthetic runs
tests/unit|contract|integration|e2e|gpu
design/  docs/  examples/  scripts/
```

## Rules
1. Keep it simple: the simplest code that meets the acceptance cases; no speculative abstractions;
   complexity <= 10 per function, ~40 lines per function, ~300 lines per module.
2. The core never imports another honeworks package or an optional extra.
3. Every port has a fake and a contract checker in `hone_lens.testing`.
4. Explicit failure: no silent errors; "could not score" is `None`, never `0`.
5. Determinism: explicit seeds; `hashlib`, never `hash()`, for ids.
6. Tests first for public behaviour; each acceptance case has a `tests/e2e/test_ac<N>_*` test; README,
   docs and examples are executed by tests. Never weaken a test to make it pass.
7. Green means `scripts/check.sh` passes; commit only when green (Conventional Commits).
8. Anything that touches the GPU runs through `scripts/gpu-lock.sh`; unload models you load.
9. A design change starts as a record in `design/changes/` (see CONTRIBUTING.md); update
   `design/current.md` and `CHANGELOG.md` with it.
10. Git: Conventional Commits, small commits. Push a branch and open a pull request only when the user asks ("push", "ship it"). Inside that pull
request's flow, reviewing it on GitHub and pushing fixes the user asked for need no new request.
Never push to `main`, tag or publish unless the maintainer asks.

## Workflow and tooling

One branch per task; a change record before a design change; tests first; the docs updated with the
code (the table in [`.claude/skills/sync-docs/SKILL.md`](.claude/skills/sync-docs/SKILL.md));
`scripts/check.sh` green; a pull request from
[`.github/pull_request_template.md`](.github/pull_request_template.md), reviewed by claude[bot]. Claude
Code users get this flow as skills, reviewer agents and hooks in [`.claude/`](.claude/); see
[`CLAUDE.md`](CLAUDE.md). Other tools: the skills are plain Markdown and can be followed as they are.
