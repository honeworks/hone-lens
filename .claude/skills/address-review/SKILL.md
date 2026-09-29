---
name: address-review
description: Act on the answers to review comments on a pull request - fix, reply, resolve threads, push. Use when the user says the review is answered or asks to handle the review comments.
argument-hint: "[pr-number]"
---

# Address a review

1. **Load the open threads** (`<n>` from `$ARGUMENTS` or `gh pr view --json number`):
   ```bash
   gh api graphql -F owner='{owner}' -F repo='{repo}' -F pr=<n> -f query='
     query($owner: String!, $repo: String!, $pr: Int!) {
       repository(owner: $owner, name: $repo) { pullRequest(number: $pr) {
         reviewThreads(first: 100) { nodes { id isResolved path line
           comments(first: 50) { nodes { databaseId author { login } body } } } } } } }'
   ```
   Also read the review bodies and PR comments: `gh pr view <n> --comments`. If suggestions
   were committed on GitHub, `git pull` first.
2. **Decide per unresolved thread**, from the latest reply by the PR's author or the maintainer:
   | The reply... | Do |
   |---|---|
   | agrees, or says "fix it" | fix it (tests first if behaviour changes), reply "Fixed in `<sha>`" |
   | disagrees, or says "won't fix" | change nothing; reply briefly; if the reviewer was wrong, note it for `learn-from-reviews` |
   | asks a question | answer with evidence: file and line, a test, a command's output |
   | has not replied | leave it open and list it in the summary |
3. **Reply in the thread**, to its first comment (`comments.nodes[0].databaseId`; GitHub doesn't accept a
   reply as the parent):
   `gh api repos/{owner}/{repo}/pulls/<n>/comments/<databaseId>/replies -f body='...'`.
4. **Resolve** a thread only when it is fixed or its reply decided it:
   `gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<id>"}) { thread { isResolved } } }'`.
5. If code changed: `sync-docs`, `verify-before-done`, commit (`fix: address review of #<n>`), and push:
   the user asked for the review to be addressed, which covers pushing to this PR's branch (AGENTS.md
   rule 10). The push makes claude[bot] review the new commits.
6. Summarize: fixed, answered, still open.
