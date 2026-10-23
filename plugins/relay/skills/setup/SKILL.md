---
name: setup
description: Set up relay - detect your CLIs and keys, choose public or private mode, connect your accounts, pick a routing pack.
disable-model-invocation: true
allowed-tools: Bash(relay *)
---

Guide the user through relay setup. Keep each message short.

1. Run `relay init`, then `relay connect presets`. Summarize what was detected in 2-3 lines.
2. Ask in one round (use AskUserQuestion if available):
   - Default mode. **public**: never stop, cheap and open models allowed. **private**: only local models and accounts whose terms say your data is not used for training.
   - Which accounts they have: ChatGPT/Codex (personal or business), Gemini Code Assist/Enterprise, a second Claude account (e.g. company), Azure OpenAI, DeepSeek API key, Ollama.
   - Routing pack: fullstack, frontend or backend-azure.
3. For each account: `relay connect add <preset> --name <short-name> [--env KEY=VALUE] --data <no-train|unverified>`.
   - Use `--data no-train` only for business/enterprise/API accounts the user confirms. Personal accounts stay `unverified`.
   - A second account of the same tool needs its own config folder plus a one-time login; the note printed by `connect add` says which env var. Tell the user the exact login command to run in their own terminal.
   - Never ask for API keys in chat. Tell the user to export the key env var in their shell profile.
4. Run `relay mode <mode>`, `relay pack add <pack>`, `relay doctor`, then `relay tasks`. Show the final routes compactly.
5. Close with two tips: `relay mode private --project` inside client repos, and `/relay:config` to change any route later.
