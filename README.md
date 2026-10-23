# Relay

**Your AI subscriptions, your rules.** Relay is a Claude Code plugin that hands grunt work to the
*other* AI accounts you already have (ChatGPT/Codex, Gemini, a company Claude account, Azure OpenAI,
DeepSeek, Ollama) so your Claude limit is spent on design, core logic and debugging.

You decide, per kind of work, which account does it:

```
db-design      -> Claude (self)          er-diagram   -> deepseek -> ollama-cloud
core-logic     -> Claude (self)          unit-tests   -> codex-work -> deepseek
ui-first-pass  -> gemini-enterprise      ui-tweak     -> deepseek -> codex-work
small-fix      -> codex-work             docs         -> ollama-local
```

- **Two modes.** `private` sends code only to local models and accounts you have marked
  `no-train`, and fails closed to Claude. `public` never stops: if one model hits a quota it moves
  to the next, down to free and open models, and finally back to Claude.
- **Multiple accounts per vendor.** `codex-work` and `codex-personal` side by side, a second Claude
  login for company work, each with its own config folder.
- **Enterprise policy.** An admin-owned policy file can force private mode, allowlist connections,
  lock config, and every delegation is written to a local audit log.
- **Almost free to keep installed.** One ~60-token skill description sits in Claude's context. No MCP
  server, no tool schemas. Routing lives in config, so changing models costs zero tokens.

## Install (60 seconds)

In Claude Code:

```
/plugin marketplace add lokeshkonjeti/relay
/plugin install relay@relay-marketplace
/relay:setup
```

`/relay:setup` detects your CLIs and API keys, asks which mode and accounts you want, and prints
your routing table. Requires Python 3.9+ (preinstalled on macOS and most Linux).

The `relay` CLI is on Claude's PATH while the plugin is enabled. To use it in your own terminal too,
ask Claude to run `relay config path`, then symlink the `cli:` path it prints into `/usr/local/bin`.

## How it works

```
You ask Claude for something
   │
   ├─ design / core logic / debugging ──────────────► Claude does it
   │
   └─ bulk reading, tests, diagrams, docs, tweaks ──► relay run <task> ...
                                                        │  mode + policy filter
                                                        ▼
                                  route: conn A ──► conn B ──► pool ──► back to Claude
                                                        │
                       Claude gets back a short answer or "wrote tests/x.py (84 lines)"
```

Claude reads only the capped result, not the 40 files the delegate read.

## Modes

| | public (default) | private |
|---|---|---|
| Who may receive code | any connection | `data=local` or `data=no-train` only |
| When a route is exhausted | tries every other allowed connection, cheapest first, then Claude | Claude does it |
| Good for | personal and open-source repos | client and company code |

Set per repo: `relay mode private --project` writes `.relay.json`, which overrides your global mode.

Every connection carries a **data class** that you confirm:

| data | meaning | example |
|---|---|---|
| `local` | never leaves your machine | Ollama local model |
| `no-train` | provider terms say inputs aren't used for training | ChatGPT Business/Enterprise, Gemini Enterprise, Azure OpenAI, Claude Team |
| `unverified` | default for new cloud connections; private mode skips it | a personal account you haven't checked |
| `may-train` | free tiers and providers that may train or retain | DeepSeek API, OpenRouter `:free` |

Relay can't verify a vendor's terms for you. `no-train` is your assertion about *your* account and
plan, so check before setting it.

## Connections

```
relay connect presets                      # codex, claude, gemini, ollama-local, ollama-cloud,
                                           # deepseek, openrouter, openai, azure-openai, cli, ...
relay connect add codex --name codex-work --env CODEX_HOME=~/.codex-work --data no-train
relay connect add claude --name claude-co --env CLAUDE_CONFIG_DIR=~/.claude-co --data no-train
relay connect add azure-openai --name corp-azure --base-url https://<res>.openai.azure.com/openai/v1 \
      --model <deployment> --data no-train
relay connect add deepseek                 # needs DEEPSEEK_API_KEY in your environment
relay connect add ollama-local --model qwen3:8b
relay connect list
```

Second account of the same tool: give it its own config folder with `--env`, then log in once in
your terminal, e.g. `CODEX_HOME=~/.codex-work codex login`. Keys are read from environment
variables; Relay never stores them.

## Tasks, roles and packs

A **task** is a kind of work: a kind (`read`, `write`, `edit`), a short description Claude sees,
and a route. Routes can name a connection, `connection:model`, a `@role`, or `self`.

```
relay tasks                                            # every task and where it routes now
relay task set er-diagram --route deepseek,ollama-cloud
relay task set ui-first-pass --route gemini-ent,codex-work
relay task set sql-tuning --kind read --desc "explain slow queries" --route codex-work
relay task set db-design --route self                  # Claude keeps it
relay role set cheap deepseek,ollama-cloud,ollama-local
relay route ui-tweak                                   # explain the route right now
```

**Roles** (`@reader`, `@cheap`, `@agent`, `@verifier`) keep packs portable. A role entry such as
`codex` matches every account built from that preset, so `codex-work` fills the `codex` slot
automatically.

**Packs** are shareable task sets. Built in: `fullstack` (default), `frontend`, `backend-azure`.

```
relay pack list
relay pack add frontend
```

Packs can only define tasks and roles, never connections. PRs with new packs are welcome
(see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Enterprise policy

Admins can drop a policy file at `/etc/relay/policy.json`,
`/Library/Application Support/relay/policy.json`, or the path in `RELAY_POLICY`:

```json
{
  "force_mode": "private",
  "lock": true,
  "allow_connections": ["corp-azure", "codex-business"],
  "allow_data": ["local", "no-train"],
  "connections": {
    "corp-azure": {"type": "api", "preset": "azure-openai", "base_url": "https://corp.openai.azure.com/openai/v1",
                   "auth": "api-key", "key_env": "AZURE_OPENAI_API_KEY", "model": "gpt-corp",
                   "data": "no-train", "cost": "paid"}
  }
}
```

`relay audit` shows what was sent where (task, connection, data class, file paths).
`relay audit --json` exports it.

## Commands

| | |
|---|---|
| `relay run <task> "<instruction>" [--paths] [--files] [--target] [--verify] [--via]` | do the work |
| `relay tasks [--brief]` / `relay route <task>` | see routing |
| `relay task set|rm`, `relay role list|set`, `relay pack list|add|rm` | customize |
| `relay connect presets|list|add|set|rm` | accounts and models |
| `relay mode [public|private] [--project]` | modes |
| `relay doctor [--probe]` / `relay stats` / `relay audit` / `relay policy` | check and report |

In Claude Code: `/relay:setup`, `/relay:config <change>`, `/relay:stats`, `/relay:handoff`.

## Safety model

- A repo's `.relay.json` can choose mode, tasks and roles and *narrow* connections. It can't add a
  connection or environment variables, so cloning a hostile repo can't make Relay run its commands.
- `.env`, keys, certificates, Terraform state and similar files are never packed into a prompt.
  Agent CLIs (Codex, Gemini, Claude) read files themselves inside their own sandboxes. Relay
  starts them read-only for read tasks and `workspace-write` (never full access) for edits.
- Delegates can't call Relay again (nested delegation is blocked).
- API edits that come back suspiciously short are rejected and never written.
- Delegated output is a draft: Claude is told to review files and diffs before relying on them.

## How it compares

| | Relay | claude-code-router | PAL MCP | claude-delegator | codex-plugin-cc |
|---|---|---|---|---|---|
| Uses your existing CLI logins, multiple accounts | yes | API keys | partly (clink) | one each | one |
| You define work categories and routes | yes | by request type | model per call | expert roles | — |
| Private mode / data classes / policy / audit | yes | — | — | — | — |
| Fallback chain on quota | yes | yes | — | retries | — |
| Context cost while idle | ~60 tokens | none (proxy) | tool schemas | MCP tools | commands |

Relay doesn't replace those tools: use PAL for multi-model debates, or claude-code-router to swap
Claude's own model.

## Development

```
python3 -m unittest discover -s tests -v       # mock API + fake codex/gemini/claude CLIs
claude plugin validate ./plugins/relay
```

The CLI is standard-library Python with no dependencies.

## License

MIT
