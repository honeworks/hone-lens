---
name: start-task
description: Start any new piece of work (feature, fix, docs, refactor) on its own branch and decide whether it needs a change record. Use as soon as the user asks for a change, before editing any file.
---

# Start a task

1. `git status`. If there are uncommitted changes that don't belong to this task, stop and ask the user
   what to do with them.
2. `git switch main && git pull --ff-only`.
3. `git switch -c <type>/<short-slug>`, named as in CONTRIBUTING.md "Pull requests" (`feat/new-scorer`).
4. Classify the task:
   - **Design change**: it changes the public API, the CLI, a guarantee in `design/current.md` §10,
     the records it reads (`docs/sources.md`) or its findings and reports (`docs/reports.md`), a port, or adds an extra or a dependency. Next: `plan-change`.
   - **Small change**: a fix that restores documented behaviour, docs, tests, a refactor. Next:
     `implement-change`; a judgment call goes into `design/decisions.md`.
5. Tell the user in one or two lines: the branch, the classification, the next step.

The whole flow is in `CLAUDE.md`.
