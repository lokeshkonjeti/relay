---
name: setup
description: Set up relay - detect your CLIs and keys, choose public or private mode, connect your accounts, pick a routing pack. Pass "fresh" to start over.
disable-model-invocation: true
argument-hint: "[fresh]"
allowed-tools: Bash(relay *)
---

Guide the user through relay setup. Keep each message short. Arguments: $ARGUMENTS

1. Run `relay init` (add `--fresh` if the arguments say fresh, start over or reset). Summarize in 2-3 lines what was added and what was "not found".
   - If something is "not found" but the user says it is installed: Claude Code keeps the PATH from when it started. Tell them to quit and reopen Claude Code, or run `relay connect set <name> bin=<full path from 'which <bin>' in their terminal>`.
2. Ask in one round (use AskUserQuestion if available):
   - Default mode. **public**: never stop, cheap and open models allowed. **private**: only local models and accounts whose terms say your data is not used for training.
   - Which accounts they have: ChatGPT/Codex (personal or business), Google (personal, or work Code Assist/Enterprise), a second Claude account (e.g. company), Ollama (app or API key), DeepSeek API key, Azure OpenAI.
   - Routing pack: fullstack, frontend or backend-azure.
3. Connect only what isn't connected yet (`relay connect list`). Pick the preset by account:
   - Google personal (free, AI Pro, AI Ultra): `antigravity` (the `agy` CLI). Google work Code Assist/Enterprise or Vertex: `gemini`. If both `gemini` and `antigravity` got added and they only have a personal account, remove gemini: `relay connect rm gemini`.
   - Ollama app signed in (`ollama signin`): `ollama-cloud`. Ollama API key in OLLAMA_API_KEY: `ollama-api`.
   - Second account of the same tool: `relay connect add <preset> --name <short-name> --env <account_env>=~/.<short-name>`; the note printed by `connect add` names the env var. Give the user the exact one-time login command to run in their own terminal.
   - `--data no-train` only for business/enterprise/API accounts the user confirms. Personal accounts stay `unverified`.
   - Never ask for API keys in chat. If a key is missing, tell the user which variable to export in `~/.zshrc`, then restart Claude Code.
4. Run `relay mode <mode>`, `relay pack add <pack>`, then `relay doctor --probe`, then `relay tasks`. Show the final routes compactly and any probe failures verbatim.
5. Ask whether to make Claude delegate without being asked in this project. If yes, append this block to `CLAUDE.md` at the repo root (create it if missing; skip if a "## Delegation (relay)" section already exists):
   ```
   ## Delegation (relay)
   Before writing tests, docs, diagrams, fixtures or boilerplate, making UI tweaks or small
   fixes, or reading 3+ large files to answer a question: run `relay tasks --brief` and
   delegate with `relay run <task> ...`. Keep architecture, core logic, data model, auth and
   debugging yourself. Review what relay returns before relying on it.
   ```
6. Close with two tips: `relay mode private --project` inside client repos, and `/relay:config` to change any route later.
