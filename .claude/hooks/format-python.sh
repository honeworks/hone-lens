#!/usr/bin/env bash
# PostToolUse (Edit, Write): keep an edited Python file formatted and lint-clean.
#
# Only files of this repository (see in-repo.sh): a file in another repository or a scratch folder is
# left as it is, since this repository's ruff settings don't apply to it.
#
# ruff formats the file and applies its fixes, including removing unused imports: the repository keeps
# none. That can change text Claude is about to edit, or drop an import whose use comes in the next
# edit, so whenever the file changed Claude is told to re-read it and to add an import in the same edit
# as the code that uses it. Problems ruff can't fix are shown too. (Exit 2 after a tool only reports to
# Claude; it blocks nothing.)
set -uo pipefail
file="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))')"
case "$file" in *.py) ;; *) exit 0 ;; esac
[ -f "$file" ] || exit 0
. "$(dirname "$0")/in-repo.sh"
in_this_repo "$file" || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"

before="$(sha256sum < "$file")"
# Fix first, then format: formatting also tidies what the fixes leave behind.
lint="$(uv run --frozen ruff check --fix --quiet "$file" 2>&1)"
lint_status=$?
uv run --frozen ruff format --quiet "$file" >/dev/null 2>&1

report=""
if [ "$before" != "$(sha256sum < "$file")" ]; then
  report="ruff reformatted $file and applied its fixes (unused imports are removed). Re-read the file before editing it again, and add an import in the same edit as the code that uses it."
fi
if [ $lint_status -ne 0 ]; then
  report="${report:+$report
}ruff found problems it can't fix:
$lint"
fi
[ -z "$report" ] && exit 0
echo "$report" >&2
exit 2
