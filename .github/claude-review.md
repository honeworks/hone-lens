# How claude[bot] reviews a pull request

This is the procedure `.github/workflows/claude-review.yml` gives to Claude in GitHub Actions. It runs on
every push to a pull request, and when someone with write access comments `@claude review` on one
(pull requests from forks get no secrets, so they are reviewed only that way). Nobody runs it by hand.

1. **The pull request.** `gh pr view <n> --json number,headRefOid,url`; the head sha is `headRefOid`.
   Earlier reviews end with `<!-- hone-review sha=<sha> -->`
   (`gh api repos/{owner}/{repo}/pulls/<n>/reviews`); take the newest marker.
   - It equals the head, and the trigger was a push: already reviewed, stop.
   - It is an ancestor of the head (`git merge-base --is-ancestor <sha> <head>`) and
     `git log --merges <sha>..<head>` is empty: review only `<sha>..<head>`.
   - Otherwise (no marker, a rebase, `main` merged into the branch, or an `@claude review` comment):
     review `origin/main...<head>`.
2. **Fresh reviewers.** Start the subagents `pr-reviewer`, `test-auditor` and `simplicity-reviewer`
   (`.claude/agents/`) in parallel, in the foreground. Give each only the pull request number, the
   commit range and "review it"; never your own opinion of the change. Wait for all three results:
   the run ends when your turn ends, so never end it before the review is posted (step 6).
3. **Triage** every finding by reading the code at that line yourself:
   - keep: real, on a line this pull request changed, worth fixing;
   - drop: false positive, older than this pull request, a nitpick, or something ruff, pyright or the
     tests catch. Note a one-line reason.
   Merge duplicates. Severity: **blocking** (bug, broken rule or guarantee, missing test for new
   behaviour, missing doc update), **should fix**, **nit**.
4. **Verdict.** `REQUEST_CHANGES` if anything blocking is kept; otherwise `COMMENT`. Never `APPROVE`:
   approving is a person's decision. With nothing kept, write "Approve" as the verdict in the body.
5. **The review**, as JSON in a temporary file:
   ```json
   {"commit_id": "<head sha>", "event": "<verdict>", "body": "<summary>",
    "comments": [{"path": "src/hone_lens/findings.py", "line": 42, "side": "RIGHT", "body": "..."}]}
   ```
   - One inline comment per kept finding: `**blocking** · AGENTS.md rule 4`, the problem, the fix. Use
     `start_line` and `line` for a range. When the fix is exact, add a GitHub suggestion block (a fenced
     block with the language `suggestion` holding the complete replacement lines).
   - The body: the verdict on the first line, the range reviewed, counts, kept findings that fit no diff
     line, the dropped findings with reasons inside `<details>`, and `<!-- hone-review sha=<head sha> -->`
     last.
6. **Post**: `gh api repos/{owner}/{repo}/pulls/<n>/reviews --method POST --input <file>`.
   - A 422 for a line outside the diff: move that comment into the body and post again.
   - If your newest earlier review requested changes and nothing blocking is kept now, dismiss it:
     `gh api repos/{owner}/{repo}/pulls/<n>/reviews/<id>/dismissals --method PUT -f message='Addressed by <head sha>'`.
