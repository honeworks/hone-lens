# Sourced by the hooks. in_this_repo FILE succeeds when FILE belongs to this repository: it is inside
# the repository at $CLAUDE_PROJECT_DIR or one of its git worktrees, and git doesn't ignore it. Files
# anywhere else (Claude's memory and plan files, scratch files, other repositories) are not ours.
# It also sets file_dir: the nearest existing folder of FILE (Write may create new folders).
in_this_repo() {
  file_dir="$(dirname "$1")"
  while [ ! -d "$file_dir" ]; do file_dir="$(dirname "$file_dir")"; done
  local here project
  # Worktrees of one repository share its common git dir.
  here="$(git -C "$file_dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  project="$(git -C "${CLAUDE_PROJECT_DIR:-.}" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  [ "$here" = "$project" ] || return 1
  ! git -C "$file_dir" check-ignore -q "$1"
}
