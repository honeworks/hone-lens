---
name: triage-issue
description: Turn a GitHub issue into work - read it, reproduce it, classify it, draft the reply. Use when the user points at an issue or asks to go through open issues.
argument-hint: "[issue-number]"
---

# Triage an issue

1. `gh issue view <n> --comments`.
2. Classify it:
   - **bug**: behaviour differs from `docs/` or `design/current.md`;
   - **feature**: new behaviour; needs a change record;
   - **question** or **docs**: the answer, and whether the docs should say it;
   - **not planned**: outside the goals and non-goals in `design/current.md` §1.
3. Bug: reproduce it with a failing test (`debug-failure`). Confirmed: `start-task` with `fix/<slug>`,
   and the PR body says `Fixes #<n>`. Not reproducible: ask the reporter for what is missing.
4. Feature that fits the goals: `start-task`, then `plan-change`, and link the issue in the record.
5. Draft the reply and the labels, and show them to the user before posting
   (`gh issue comment`, `gh issue edit --add-label`). Nothing is posted without the user's OK.
