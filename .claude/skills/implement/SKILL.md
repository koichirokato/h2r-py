---
name: implement
description: Write code for an already-agreed plan, with baseline tests, and verify it locally (make check + pre-commit) before considering it done. Use once a plan from scope-work is confirmed.
context: fork
agent: implement
---

Implement the agreed plan, with baseline tests covering the happy path. Exhaustive edge-case
testing is the `test-review` skill's job, not yours — don't over-invest here.

Before reporting the work as finished:

1. Run `make check` (ruff + ty + pytest via Docker) and fix everything it flags.
2. Run `uvx pre-commit run --all-files` and fix everything it flags.
3. Commit locally with a `type(scope): message` message.

Do not push the branch or run any `gh` command — that's the `ship-pr` skill's job, later in the
flow. This boundary is a convention (the underlying agent does have Bash), not a tool
restriction — follow it anyway so a human gets a checkpoint between local verification and
shipping. Point the user at `test-review` next, not `ship-pr` directly.
