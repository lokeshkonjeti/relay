---
name: config
description: Change which model or account handles each kind of work, switch public/private mode, or add a task category.
disable-model-invocation: true
argument-hint: "[e.g. 'send er-diagram to deepseek' or 'make this repo private']"
allowed-tools: Bash(relay *)
---

Request: $ARGUMENTS

1. Run `relay tasks` and `relay connect list`. If nothing was requested, show both compactly, suggest the 3 most useful changes and stop.
2. Apply the request:
   - Route a task: `relay task set <task> --route <conn>,<fallback>` (`self` = Claude does it, `@role` = a role chain, `conn:model` = model override). Add `--project` to scope it to this repo.
   - New task category: `relay task set <name> --kind read|write|edit --desc "<under 8 words>" --route ...`
   - Role chain: `relay role set cheap deepseek,ollama-cloud`
   - Mode: `relay mode public|private [--project]`
   - Account data terms: `relay connect set <conn> data=no-train|unverified|may-train|local`
   - Verifier for a task: `relay task set <task> --verify <conn>`
3. Run `relay route <task>` for each task you changed and confirm in one line.
