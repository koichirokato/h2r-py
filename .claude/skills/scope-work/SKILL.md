---
name: scope-work
description: Investigate and propose a plan for a new task before any code is written. Use at the start of any non-trivial feature, bugfix, or refactor request in this repo, before touching files — including when the user's phrasing sounds like a direct instruction ("implement X", "add Y", "fix Z") but no plan has been agreed yet.
context: fork
agent: scope
---

Research the codebase for the requested change and propose a concrete plan: what will change,
in which files, and any open design decisions. Phrase it as something the user can redirect, not
a final decision.

Do not write or edit any file in this phase — the underlying agent has no Edit/Write/Bash tools,
so this is structural, not just an instruction.

End by asking the user to confirm before implementation starts. Point them at the `implement`
skill once they do.
