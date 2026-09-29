---
name: open-pr
description: Push the task branch, open a pull request with a full description, then wait for claude[bot]'s review. Use when the user says the work is ready - "push", "ship it", "open a PR", "I'm happy with this".
---

# Open a pull request

1. Not on `main`. `verify-before-done` has passed on the current `HEAD`; if anything changed since, run
   it again.
2. `git push -u origin HEAD`.
3. Write the body from `.github/pull_request_template.md` into a temporary file. Fill every section from
   the commits, the change record and the `scripts/check.sh` output. Title: the Conventional Commit
   subject of the main change, at most 72 characters.
4. `gh pr create --base main --title "<title>" --body-file <file>`. If the branch already has a PR,
   update it: `gh pr edit --body-file <file>`.
5. claude[bot] reviews the pull request on GitHub (`.github/workflows/claude-review.yml`). Wait for the
   checks with `gh pr checks <n> --watch`, then summarize the review for the user
   (`gh pr view <n> --json reviews`): verdict, one line per finding, the link.
