---
name: setup
description: Set up relay in one step - detect and test every account, set up this repo. Args: private, fresh, network=local.
disable-model-invocation: true
argument-hint: "[private] [fresh] [network=local]"
allowed-tools: Bash(relay *)
---

Arguments: $ARGUMENTS

1. Run `relay up`, adding flags from the arguments: `private` → `--private`, `fresh` → `--fresh`, `network=local|on|off` → `--network <value>`. If the user mentioned this is client or company code, add `--private`.
2. Show the output as-is in a code block.
3. Then at most three short lines, only for what applies:
   - An account marked ✗: what to do for that error (log in, export a key in `~/.zshrc` and restart Claude Code, or install). Personal Google account plus a ✗ gemini: use antigravity, ignore gemini. Then rerun `/relay:setup`.
   - Codex is ✓ and the project's tests need a database: suggest rerunning with `network=local`.
   - A business/enterprise account that should be allowed in private mode: `relay connect set <name> data=no-train`.
Never ask for API keys in chat.
