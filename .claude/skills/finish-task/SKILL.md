---
name: finish-task
description: Clean up after a pull request is merged - update main, delete the branch, check the change record status. Use when the user says a PR is merged or asks to clean up.
---

# Finish a task

1. `gh pr view <n> --json state,mergedAt,headRefName,headRefOid`: it must be merged.
2. `git switch main && git pull --ff-only`.
3. Delete the local branch only if it holds nothing the pull request didn't have: when
   `git rev-parse <branch>` equals `headRefOid`, `git branch -D <branch>` (`-d` refuses after a squash or
   rebase merge). Otherwise the branch has commits that were never merged: stop, list them
   (`git log --oneline <headRefOid>..<branch>`) and ask the user.
   `git push origin --delete <branch>` if the branch is still on GitHub.
4. If the PR implemented a change record whose status is not `implemented in <version>`, fix it on a new
   small branch (`start-task`, type `docs`).
5. Tell the user in one line.
