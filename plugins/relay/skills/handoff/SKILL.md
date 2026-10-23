---
name: handoff
description: Write HANDOFF.md so another agent or session can continue this task when Claude's limit is close.
disable-model-invocation: true
argument-hint: "[optional note]"
---

Write `HANDOFF.md` at the repo root (overwrite). Keep it under 40 lines. Note from the user: $ARGUMENTS

- **Goal**: one sentence.
- **Done**: bullets with file paths.
- **Next**: numbered steps, concrete enough to execute without this conversation.
- **Decisions & constraints**: what a fresh agent would get wrong.
- **State**: failing tests, uncommitted work, commands to run.

Then tell the user in one line that any agent can continue with: "read HANDOFF.md and continue".
