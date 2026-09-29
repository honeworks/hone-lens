---
name: verify-before-done
description: Prove the work is done with command output before saying so - scripts/check.sh green, everything committed, docs synced. Use before telling the user something is finished, fixed or passing, and before open-pr.
---

# Verify before saying "done"

Evidence first, claims after. "Should work" is not a result.

1. `scripts/check.sh` (it unsets `VIRTUAL_ENV` itself). It must exit 0. On failure fix the cause, never
   weaken a test, and run it again.
2. If the change touches real-model code paths, run `real-model-tests`, or say plainly that they were not
   run and why.
3. `git status` shows nothing uncommitted.
4. The `sync-docs` table has no unmet row.
5. Report with the evidence: the command, and the lines that show the result (tests passed, coverage,
   build and wheel smoke test). Say what was not verified.

Adapted from the `verification-before-completion` skill of
[obra/superpowers](https://github.com/obra/superpowers) (MIT).
