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

## Quick start

1. **Install and log in to the accounts you want to use** ([below](#install-and-log-in)). Do this
   first: Relay can only route to tools it can find.
2. **Quit and reopen Claude Code** so it picks up the new PATH and environment variables.
3. In Claude Code:
   ```
   /plugin marketplace add lokeshkonjeti/relay
   /plugin install relay@relay-marketplace
   /relay:setup
   ```
   That's it. `/relay:setup` runs **`relay up`**, which in one step:
   - finds your installed CLIs and API keys and connects them
   - sends each account a one-word test prompt (in parallel) and switches off any that fail, with
     the reason shown
   - adds a short delegation rule to the project's `CLAUDE.md`, so Claude delegates without being
     asked
   - prints which tasks go where

   Options: `/relay:setup private` for client or company repos, `/relay:setup network=local` so
   Codex can run tests against your local database, `/relay:setup fresh` to start over (your old
   config is backed up). The same flags work in a terminal: `relay up --private --network local`.

   Rerun it any time you install, log in to or fix an account. It re-tests everything, turns fixed
   accounts back on, and never duplicates anything.

Requires Python 3.9+ (preinstalled on macOS and most Linux).

The `relay` CLI is on Claude's PATH while the plugin is enabled. To use it in your own terminal too,
ask Claude to run `relay config path`, then alias the `cli:` path it prints in your shell profile:
`alias relay="<that path>"`.

## Install and log in

You only need the ones you'll use. Keys go in your shell profile (`~/.zshrc` on macOS), never in a
repo. Restart Claude Code after changing it.

| Account | Install | Log in once | Relay preset |
|---|---|---|---|
| **ChatGPT / Codex** | `npm i -g @openai/codex` | `codex login` | `codex` |
| **Google, personal** (free, AI Pro, AI Ultra) | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` | `agy` | `antigravity` |
| **Google, work** (Code Assist Standard/Enterprise, Vertex) | `npm i -g @google/gemini-cli` | `gemini`, then pick your work account | `gemini` |
| **Second Claude account** (e.g. company) | already installed | `CLAUDE_CONFIG_DIR=~/.claude-work claude` | `claude` with `--env CLAUDE_CONFIG_DIR=~/.claude-work` |
| **Ollama Cloud via the app** | [ollama.com/download](https://ollama.com/download) | `ollama signin` | `ollama-cloud` |
| **Ollama Cloud via API key** | nothing | key from [ollama.com/settings/keys](https://ollama.com/settings/keys) → `export OLLAMA_API_KEY=...` | `ollama-api` |
| **Local models** | Ollama app | `ollama pull qwen3:8b` | `ollama-local --model qwen3:8b` |
| **DeepSeek** | nothing | key from platform.deepseek.com → `export DEEPSEEK_API_KEY=...` | `deepseek` |
| **Azure OpenAI** | nothing | `export AZURE_OPENAI_API_KEY=...` | `azure-openai --base-url https://<res>.openai.azure.com/openai/v1 --model <deployment>` |

Things that trip people up:

- **Personal Google accounts can't use Gemini CLI.** Since June 18, 2026, Gemini CLI says *"This
  client is no longer supported for Gemini Code Assist for individuals"*. Use Antigravity (`agy`)
  instead.
- **"command not found" right after installing.** Installers add their folder (e.g.
  `~/.local/bin`) to `~/.zshrc`; open a new terminal or run `source ~/.zshrc`.
- **Relay says a CLI is "not found" but it works in your terminal.** Claude Code keeps the PATH it
  started with. Restart it, or pin the path: `relay connect set codex bin=$(which codex)`.
- **Antigravity hangs.** `agy -p` can hang when run from a script
  ([issue #318](https://github.com/google-antigravity/antigravity-cli/issues/318)); Relay caps each
  call at about 6 minutes and falls back. If it keeps happening, `relay connect rm antigravity`.
- **Two accounts of the same tool.** Give each its own config folder and log in once per folder,
  e.g. `CODEX_HOME=~/.codex-work codex login`, then
  `relay connect add codex --name codex-work --env CODEX_HOME=~/.codex-work`.

After installing or logging in to anything, run `relay up` (or `/relay:setup`) again.

### Making Claude delegate without being asked

A skill alone only nudges Claude: without a rule in the project it tends to do everything itself
unless you say "use relay to ...". So `relay up` adds this block to the project's `CLAUDE.md`
once, and never twice. It costs about 70 tokens per session. Skip it with
`relay up --no-claude-md`, or paste it into other repos yourself:

```markdown
## Delegation (relay)
Before writing tests, docs, diagrams, fixtures or boilerplate, making UI tweaks or small
fixes, or reading 3+ large files to answer a question: run `relay tasks --brief` and
delegate with `relay run <task> ...`. Keep architecture, core logic, data model, auth and
debugging yourself. Review what relay returns before relying on it.
```

Watch delegations as they happen: `tail -F ~/.config/relay/audit.jsonl`.

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
relay connect presets                      # codex, claude, gemini, antigravity, ollama-local,
                                           # ollama-cloud, ollama-api, deepseek, azure-openai, ...
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

### Project environment: detected automatically

Agents only help if they can run what they write. Before each task, Relay looks at the repo and
tells the agent how this project runs its tests:

- **Python:** the project's virtualenv (`.venv`, `venv`, also inside `backend/` and similar
  folders), or `uv`, Poetry or Pipenv
- **JS/TS:** `npm`, `pnpm`, `yarn` or `bun` (from the lockfile), plus `typecheck`/`lint` scripts
- **Java, Go, .NET:** Maven (`mvnw`), Gradle (`gradlew`), `go test`, `dotnet test`
- **Local services:** whether the database, cache or queue in `.env` or `docker-compose` points at
  `localhost`. Only the host is checked; passwords and keys in `.env` are never read into prompts.

When an agent writes a test file, it's told to run it with that command and fix it until it
passes, so Claude doesn't have to. See what Relay detected, and correct it if needed:

```
relay env                              # what Relay detected for this repo
relay env set test="make test"         # override (stored in your global config, not the repo)
relay env clear
```

This is passed in the instructions sent with each task rather than written to `AGENTS.md`, so it
works for every agent and adds nothing to your repo.

### Letting agents run tests that need a database

Codex's sandbox blocks all network by default, even `localhost`. Relay's default for Codex is
`network=auto`: if the project uses local services, Codex gets **localhost-only** network on write
and edit runs (reads never get network), so it can run tests against your dev database. Otherwise
it gets none. To choose explicitly:

```
relay connect set codex network=auto    # default: local when the project needs it, else off
relay connect set codex network=local   # always localhost only (through Codex's network proxy)
relay connect set codex network=on      # unrestricted internet access
relay connect set codex network=off     # never
```

`local` is the safer choice: code Codex runs can reach your dev database but not the internet,
which limits what a prompt-injected dependency or file could do. It relies on Codex's
`network_proxy` feature. If your database URL uses a Unix socket instead of `localhost:5432`,
switch to `localhost` or use `on`.

For other CLI flags, `extra_args` adds raw arguments per task kind:
`relay connect set codex 'extra_args={"write": ["-c", "model_reasoning_effort=low"]}'`.

### Seeing what it did and saved

```
tail -F ~/.config/relay/audit.jsonl   # watch delegations live, one line per finished call
relay audit --days 1                  # what was sent where today: task, account, data class, files
relay stats --days 7                  # per account: tasks, checks, Claude tokens saved, tokens it used
relay stats --hours 1                 # just this session
```

Example:

```
account          tasks checks failed   Claude saved   tokens it used (in / cached / out)
ollama-api           1      0      1         ~37.7k   37.6k / 0 / 1.6k
codex                2      1      0         ~24.2k   181.0k / 140.0k / 10.0k
antigravity          1      0      0          ~2.8k   0 / 0 / 0  (1 call(s) not reported)
```

Token use is reported by API accounts (Ollama, DeepSeek, Azure) and by Codex. Antigravity, Gemini
and Claude CLIs don't report it, so those show as "not reported". Cached input is the part the
provider served from its prompt cache, which is usually cheaper or free against your quota.

The saving per call is an estimate: what the delegate read, plus what it produced (the file or
diff), minus the short reply Claude reads back, divided by 4 to convert characters to tokens.
For agent CLIs like Codex, which open files themselves, "read" means the files Relay pointed them
at. The number is measured before Claude reviews the result, and reviewing costs some tokens back.
Expect the biggest savings from bulk reading and test writing, and the smallest from tiny edits.

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
| `relay up [--private] [--network local] [--fresh]` | one-step setup and re-test |
| `relay env [set test="..." \| clear]` | how this project runs its tests (auto-detected) |
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

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT
