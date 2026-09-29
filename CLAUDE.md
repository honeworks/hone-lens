@AGENTS.md

## Claude Code

Every task follows one flow. Each step is a skill in `.claude/skills/`:

```text
new task      -> start-task (branch) -> design change? plan-change, then wait for approval
              -> implement-change (tests first) -> sync-docs -> verify-before-done
"push" / "ship it" / "I'm happy"
              -> open-pr -> claude[bot] reviews it on GitHub
answers on GitHub -> address-review -> push -> claude[bot] reviews the new commits
merged        -> finish-task
```

- Never edit on `main`: `.claude/hooks/guard-main.sh` blocks the Edit/Write tools on this repository's
  files there (`HONE_ALLOW_MAIN=1` overrides it). Shell edits and commits are not blocked, so start
  every task with `start-task`. On GitHub, rulesets protect `main` (pull requests only, CI green, every
  review thread resolved, no force push or deletion) and `v*` tags (maintainer only); task branches may
  be force-pushed.
- Reviews run only on GitHub: `.github/workflows/claude-review.yml` has claude[bot] follow
  `.github/claude-review.md` with the reviewer agents on every push to a pull request, using the
  maintainer's Claude subscription. For a pull request from a fork, comment `@claude review`. A branch
  pushed without a pull request gets a reminder from `.claude/hooks/after-push.sh`.
- More skills: `debug-failure`, `add-example`, `add-adapter`, `deprecate-and-migrate`,
  `real-model-tests`, `triage-issue`, `learn-from-reviews`, and `release` (only when the maintainer asks).
- Reviewer agents in `.claude/agents/` (used by claude[bot]): `pr-reviewer`, `test-auditor`,
  `simplicity-reviewer`.
- Recommended plugins, enabled in `.claude/settings.json`; install them once with
  `/plugin install pyright-lsp@claude-plugins-official` and
  `/plugin install pr-review-toolkit@claude-plugins-official`.
- `.claude/settings.json` (JSON, so it can't hold comments): `uv run *` is allowed without asking,
  because development means running code (tests, scripts, tools). Its deny rules for `uv publish` and
  `twine` are a reminder, not a lock: `uv run` can start anything. Releases go through
  `.github/workflows/release.yml` and PyPI's trusted publishing, never from a laptop. To be asked
  before commands run, override `permissions` in `.claude/settings.local.json`.
- "The user" in the skills is the person in the session; "the maintainer" is whoever maintains
  honeworks/hone-lens. Only the maintainer accepts change records, merges and releases.
- Settings for your machine (model names, `HONE_TEST_*`, extra permissions)
  go in `.claude/settings.local.json`, which is not committed.
