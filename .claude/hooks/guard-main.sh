#!/usr/bin/env bash
# PreToolUse (Edit, Write, NotebookEdit): refuse to edit this repository's files on main, so every task
# happens on its own branch (the start-task skill).
#
# - Only files of this repository are guarded. Files anywhere else (Claude's memory and plan files,
#   scratch files, other repositories) and git-ignored files (.claude/settings.local.json) are allowed.
# - The branch is read where the edited file is, so a git worktree on a task branch works even while the
#   main checkout is on main.
# - Only the file-edit tools pass through this hook: shell edits and commits are not blocked.
# - Override for one session with HONE_ALLOW_MAIN=1.
set -uo pipefail
[ "${HONE_ALLOW_MAIN:-}" = "1" ] && exit 0

file="$(python3 -c 'import json, sys
i = json.load(sys.stdin).get("tool_input", {})
print(i.get("file_path") or i.get("notebook_path") or "")')"
[ -n "$file" ] || exit 0
. "$(dirname "$0")/in-repo.sh"
in_this_repo "$file" || exit 0

branch="$(git -C "$file_dir" branch --show-current)"
if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
  echo "You are on '$branch'. Start the task on its own branch first (the start-task skill)." >&2
  exit 2
fi
exit 0
