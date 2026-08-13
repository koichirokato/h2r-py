---
name: ship-pr
description: Push an already-committed, locally-verified branch and open a PR, then watch CI. Never merges — merging always requires the user to run it themselves. Use once the implement skill reports a green local commit.
context: fork
agent: ship
---

Push the current branch, open a PR (`gh pr create`) with a Summary + Test plan body, then poll
`gh pr checks <number>` until CI resolves and report the result.

Never run `gh pr merge` proactively — every invocation requires a human confirmation prompt via
`permissions.ask` in `.claude/settings.json`, and merging is the user's call alone, made
explicitly and in the moment for that specific PR.

If CI fails, report the failure and stop; do not attempt to fix code here (this agent has no
Edit/Write tools).
