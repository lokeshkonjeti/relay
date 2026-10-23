# Changelog

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
