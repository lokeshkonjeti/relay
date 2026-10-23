---
name: delegate
description: Save Claude usage by routing grunt work (bulk reading, tests, diagrams, docs, UI tweaks, small fixes) to the user's other models via `relay`. Load before reading 3+ or large files, or writing tests, docs, diagrams or boilerplate.
---

Routing from the user's config:
!`"${CLAUDE_PLUGIN_ROOT}/bin/relay" tasks --brief 2>/dev/null || relay tasks --brief 2>/dev/null || echo "relay is not configured; suggest /relay:setup and do the work yourself"`

How to use it:
- Pick the task whose description fits. Tasks listed as Claude's by design stay with you.
- read: `--paths <files|dirs>`. Trust the summary instead of re-reading. Add `--verify` when the answer drives an edit or a decision.
- write: `--target <new file>` and `--paths <reference files>` for conventions. Then review the file and fix only what's wrong.
- edit: `--files <files>`. Then check `git diff` on those files.
- Output starting with `SELF` means no delegate is available or allowed: do it yourself and mention it once.
- Never send .env files, keys or anything the user called confidential. Never try to get around private mode.
