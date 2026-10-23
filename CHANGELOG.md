# Changelog

## 0.3.2
- **Detects how each project runs its tests**: Python venvs (including `backend/.venv`), uv,
  Poetry, Pipenv, npm/pnpm/yarn/bun scripts, Maven, Gradle, Go and .NET. Relay passes this to
  every agent with each task. Agents now run the test files they write with the right interpreter
  and fix them until they pass. `relay env` shows what was detected, and `relay env set` overrides it.
- Codex network defaults to `auto`: localhost-only on write and edit runs when the project uses
  local services such as a dev database, and none otherwise. Only `.env` hosts are checked, never
  secret values.
- Fix: a path that doesn't exist no longer fails the whole call (which forced a slow, costly
  fallback). It's skipped with a note.
- Fix: answers longer than the output cap are saved in full to `~/.config/relay/outputs/` and
  Claude is pointed to the file, instead of the answer being cut off and the task re-run.
- Fix: failed `--verify` calls are written to the audit log and counted in `relay stats`.

## 0.3.1
- `relay stats` breaks savings down per account: tasks, `--verify` checks, failures, Claude tokens
  saved, and tokens each account used (input / cached / output). `--hours N` covers a short window
  such as one test session.
- Codex runs now record their real token usage (via `codex exec --json`). Older Codex versions
  without `--json` are retried without it.
- Verifier calls log their token usage too.

## 0.3.0
- **`relay up`**: one-command setup and re-test. It finds installed CLIs and API keys, tests every
  account in parallel, switches off failing accounts (with the reason) and back on once they
  pass, adds the delegation rule to the repo's `CLAUDE.md`, and prints where each task goes.
  Flags: `--private`, `--network local|on|off`, `--fresh`, `--no-probe`, `--no-claude-md`.
  `/relay:setup` now just runs it.

## 0.2.3
- `relay connect set codex network=local|on|off` lets Codex reach a dev database (localhost only,
  or fully) when writing or editing, so it can run the tests it writes. Reads never get network.
- `extra_args`: raw per-task-kind arguments for any CLI connection.
- Fix: `relay stats` undercounted savings for Codex and other agent CLIs, and for every write
  task. The audit now records what was read, what was produced and what Claude read back.
- `er-diagram` routes to an agent (Codex) first: relationship cardinality needs reasoning.

## 0.2.2
- Fix: Markdown files containing Mermaid or code blocks were cut off after their first heading.
- Empty or cut-off model output is rejected, and the task falls back to the next account.
- `/relay:setup` can add a delegation rule to the project's `CLAUDE.md`.

## 0.2.1
- Presets: `antigravity` (the `agy` CLI, for personal Google accounts) and `ollama-api`
  (Ollama Cloud with `OLLAMA_API_KEY`).
- Per-connection timeouts are honoured. CLIs are found outside Claude Code's PATH.
- `relay init --fresh`. README: install and log-in guide.

## 0.2.0
- Per-task routing to your own accounts (Codex, Gemini, a second Claude, Azure OpenAI, DeepSeek,
  Ollama), public and private modes, enterprise policy, audit log, routing packs.
