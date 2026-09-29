#!/usr/bin/env bash
# PostToolUse (git push, gh pr create): remind Claude when a pushed branch has no open pull request (the
# open-pr skill). Reviews need no reminder: claude[bot] reviews every push to a pull request on GitHub.
# Stays silent when GitHub can't be asked (offline, logged out, rate-limited): a failed call is never
# taken to mean "no pull request".
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}"
branch="$(git branch --show-current 2>/dev/null)"
case "$branch" in ""|main|master) exit 0 ;; esac
command -v gh >/dev/null || exit 0

if ! pr="$(gh pr view "$branch" --json state -q 'select(.state == "OPEN") | .state' 2>&1)"; then
  case "$pr" in
    *"no pull requests found"*) pr="" ;;   # gh answered: the branch has no pull request
    *) exit 0 ;;                           # gh failed: say nothing rather than guess
  esac
fi
[ -n "$pr" ] && exit 0
python3 -c 'import json, sys; print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
    "additionalContext": sys.argv[1]}}))' \
  "Branch '$branch' is pushed but has no open pull request: run the open-pr skill."
