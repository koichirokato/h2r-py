---
name: test-review
description: Independently hunt for missing edge cases and silent-failure risks in code the implement skill already wrote and verified locally. Use once implement reports a green local commit, before /code-review or ship-pr.
context: fork
agent: test
---

Act as a skeptical second pair of eyes on the implementation, not as its author. Read the code
and its existing tests, then prioritize by how quietly a bug could fail: off-by-one errors,
boundary values, and wrong-but-plausible return values before exception-raising failures.

Add or strengthen tests for edge cases, boundary values, and error paths. For every new test,
confirm it actually fails against the current code (or exercises a real gap) before it's fixed —
a test that never goes red proves nothing. Run `make check` and `uvx pre-commit run --all-files`
after your changes; both must be green before you're done.

If you find real implementation bugs, report them precisely (file, line, failure scenario) and
stop — hand back to `implement`, do not fix them yourself. If tests are solid and nothing else
was found, say so and point the user at `/code-review` next, not `ship-pr` directly.
