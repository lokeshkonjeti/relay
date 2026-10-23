"""relay command line."""
import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__, backends, engine
from .config import (COST_RANK, DATA_CLASSES, MODES, TASK_KINDS, Config, RelayError, audit_file,
                     global_file, list_packs, load_json, load_pack, presets, project_file, save_json)

ROLE_DEFAULTS = {
    "reader": ["ollama-cloud", "ollama-local", "deepseek", "codex"],
    "cheap": ["deepseek", "ollama-cloud", "ollama-local", "openrouter", "codex"],
    "agent": ["codex", "gemini"],
    "verifier": ["codex", "gemini"],
}


def _parse_value(v):
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def _split(v):
    return [x.strip() for x in v.split(",") if x.strip()] if isinstance(v, str) else v


def _global():
    return load_json(global_file())


def _require_unlocked(cfg, what):
    if cfg.locked():
        raise RelayError(f"{what} is locked by enterprise policy {cfg.policy['_path']}")


# ---------------------------------------------------------------- run

def cmd_run(a, cfg):
    if int(os.environ.get("RELAY_DEPTH", "0") or 0) >= 1:
        print("[relay] SELF: nested delegation blocked (relay was called from inside a delegate). "
              "Do this task yourself.")
        return
    res = engine.run_task(cfg, a.task, a.instruction, paths=a.paths, files=a.files,
                          target=a.target, force=a.force, via=a.via, verify=a.verify)
    head = f"[relay {cfg.mode} · {a.task} → "
    if res.conn == engine.SELF:
        body = f"{head}SELF] Do this task yourself ({res.text})."
    else:
        body = f"{head}{res.conn}]\n{res.text}"
    if res.notes:
        body += "\nnotes: " + "; ".join(res.notes)
    print(engine.cap(body, cfg.settings))


# ---------------------------------------------------------------- tasks / routes

def cmd_tasks(a, cfg):
    designated, unavailable, rows = [], [], []
    for name in sorted(cfg.tasks):
        t = cfg.tasks[name]
        cands, _ = engine.resolve(cfg, name)
        if cands[0][0] == engine.SELF:
            (designated if t.get("route", [None])[0] == engine.SELF else unavailable).append(name)
            continue
        chain = [c[0] + (f":{c[1]}" if c[1] else "") for c in cands if c[0] != engine.SELF]
        rows.append((name, t.get("kind", "read"), t.get("desc", ""), chain))
    if a.brief:
        print(f"relay mode: {cfg.mode}")
        if designated:
            print(f"Claude's by design (don't delegate): {', '.join(designated)}")
        if unavailable:
            print(f"no delegate available now, do yourself: {', '.join(unavailable)}")
        if rows:
            print('delegate with: relay run <task> "<instruction>" [--paths ..|--files ..|--target ..] [--verify]')
            generic = [r for r in rows if r[0] in ("read", "write", "edit")]
            for name, kind, desc, chain in rows:
                if r_is_generic(name) and len(rows) > len(generic):
                    continue
                more = f" (+{len(chain) - 1} fallback)" if len(chain) > 1 else ""
                print(f"  {name} [{kind}] {desc} -> {chain[0]}{more}")
            if generic and len(rows) > len(generic):
                print(f"  generic: {', '.join(r[0] for r in generic)} (use when no task above fits)")
        return
    print(f"mode: {cfg.mode} ({cfg.mode_source()})")
    for name in sorted(cfg.tasks):
        t = cfg.tasks[name]
        cands, skipped = engine.resolve(cfg, name)
        chain = " -> ".join(c[0] + (f":{c[1]}" if c[1] else "") for c in cands)
        print(f"{name:<20} {t.get('kind', 'read'):<5} {chain}")
        if a.verbose:
            print(f"{'':<20} route={t.get('route')} desc={t.get('desc', '')}")
            for s in skipped:
                print(f"{'':<20} skipped {s}")


def r_is_generic(name):
    return name in ("read", "write", "edit")


def cmd_task(a, cfg):
    target = project_file() if a.project else global_file()
    data = load_json(target)
    tasks = data.setdefault("tasks", {})
    if a.action == "rm":
        if a.name not in tasks:
            raise RelayError(f"task {a.name} is not defined in {target} (pack/builtin tasks can be overridden, not removed)")
        del tasks[a.name]
    else:
        t = tasks.setdefault(a.name, dict(cfg.tasks.get(a.name, {})))
        if a.route:
            t["route"] = _split(a.route)
        if a.kind:
            t["kind"] = a.kind
        if a.desc is not None:
            t["desc"] = a.desc
        if a.verify is not None:
            if a.verify in ("", "none", "off"):
                t.pop("verify", None)
            else:
                t["verify"] = a.verify
        t.setdefault("kind", "read")
        if "route" not in t:
            raise RelayError("new task needs --route (e.g. --route deepseek,codex or --route self)")
    save_json(target, data)
    print(f"saved {target}")


def cmd_role(a, cfg):
    if a.action == "list":
        for r, chain in sorted(cfg.roles.items()):
            print(f"@{r:<10} {' -> '.join(chain)}")
        return
    target = project_file() if a.project else global_file()
    data = load_json(target)
    data.setdefault("roles", {})[a.name.lstrip("@")] = _split(a.chain)
    save_json(target, data)
    print(f"saved {target}")


def cmd_route(a, cfg):
    cands, skipped = engine.resolve(cfg, a.task, a.via)
    print(f"{a.task} in {cfg.mode} mode:")
    for i, (n, m) in enumerate(cands, 1):
        conn = cfg.connections.get(n, {})
        extra = f"data={conn.get('data')} cost={conn.get('cost')}" if conn else "Claude itself"
        print(f"  {i}. {n}{':' + m if m else ''}  ({extra})")
    for s in skipped:
        print(f"  skipped {s}")


# ---------------------------------------------------------------- connections

def cmd_connect(a, cfg):
    pre = presets()
    if a.action == "presets":
        for k, v in pre.items():
            exp = " [experimental]" if v.get("experimental") else ""
            print(f"{k:<18} {v['type']:<4} data={v.get('data'):<10} cost={v.get('cost'):<12}{exp}\n{'':<18} {v.get('note', '')}")
        return
    if a.action == "list":
        if not cfg.connections:
            print("no connections yet: run `relay init` or `relay connect add <preset>`")
        for n, c in sorted(cfg.connections.items()):
            ok, why = backends.availability(n, c)
            allowed, reason = cfg.connection_allowed(n)
            state = "ready" if ok else f"unavailable: {why}"
            pol = "" if allowed else f"  [{reason}]"
            model = f" model={c['model']}" if c.get("model") else ""
            acct = f" env={','.join(c.get('env', {}))}" if c.get("env") else ""
            print(f"{n:<16} {c.get('type'):<4} data={c.get('data', 'unverified'):<10} cost={c.get('cost', '?'):<12}{model}{acct}  {state}{pol}")
        return
    _require_unlocked(cfg, "changing connections")
    data = _global()
    conns = data.setdefault("connections", {})
    if a.action == "add":
        if a.preset not in pre:
            raise RelayError(f"unknown preset {a.preset}. Try: {', '.join(pre)}")
        name = a.name or a.preset
        if ":" in name or "@" in name:
            raise RelayError("connection names cannot contain ':' or '@'")
        if name in conns and not a.force:
            raise RelayError(f"connection {name} exists (use --force or `relay connect set`)")
        c = {k: v for k, v in pre[a.preset].items() if k not in ("note", "experimental")}
        c["preset"] = a.preset
        for field in ("model", "data", "cost", "bin"):
            v = getattr(a, field)
            if v:
                c[field] = v
        if a.base_url:
            c["base_url"] = a.base_url
        if a.key_env:
            c["key_env"] = a.key_env
        if a.kinds:
            c["kinds"] = _split(a.kinds)
        if a.env:
            c["env"] = dict(e.split("=", 1) for e in a.env)
        if c.get("data") not in DATA_CLASSES:
            raise RelayError(f"data must be one of {DATA_CLASSES}")
        conns[name] = c
        save_json(global_file(), data)
        print(f"added {name} ({a.preset}, data={c['data']}, cost={c.get('cost')})")
        if c["data"] == "unverified":
            print(f"  private mode will skip it until you confirm the account's data terms:\n"
                  f"  relay connect set {name} data=no-train")
        note = pre[a.preset].get("note")
        if note:
            print(f"  note: {note}")
    elif a.action == "set":
        if a.preset not in conns:
            raise RelayError(f"no connection {a.preset} in {global_file()}")
        c = conns[a.preset]
        for pair in a.pairs:
            k, _, v = pair.partition("=")
            if k.startswith("env."):
                c.setdefault("env", {})[k[4:]] = v
            elif k == "kinds":
                c[k] = _split(v)
            elif k == "data" and v not in DATA_CLASSES:
                raise RelayError(f"data must be one of {DATA_CLASSES}")
            elif k == "cost" and v not in COST_RANK:
                raise RelayError(f"cost must be one of {list(COST_RANK)}")
            else:
                c[k] = _parse_value(v) if k not in ("model", "bin", "base_url", "key_env") else v
        save_json(global_file(), data)
        print(f"updated {a.preset}")
    elif a.action == "rm":
        if conns.pop(a.preset, None) is None:
            raise RelayError(f"no connection {a.preset}")
        save_json(global_file(), data)
        print(f"removed {a.preset}")


# ---------------------------------------------------------------- mode / packs / policy

def cmd_mode(a, cfg):
    if not a.mode:
        allowed = ", ".join(cfg.allowed_data())
        print(f"{cfg.mode} (from {cfg.mode_source()}); data classes allowed: {allowed}")
        return
    if cfg.policy.get("force_mode"):
        raise RelayError(f"mode is forced to {cfg.policy['force_mode']} by {cfg.policy['_path']}")
    target = project_file() if a.project else global_file()
    data = load_json(target)
    data["mode"] = a.mode
    save_json(target, data)
    print(f"{a.mode} mode saved to {target}")
    if a.mode == "private":
        print("  only connections with data=local or data=no-train are used; everything else falls back to Claude itself.")


def cmd_pack(a, cfg):
    if a.action == "list":
        active = _global().get("packs", [])
        for p in list_packs():
            d = load_pack(p)
            mark = "*" if p in active else " "
            print(f"{mark} {p:<14} {d.get('description', '')}")
        return
    data = _global()
    packs = data.setdefault("packs", [])
    if a.action == "add":
        load_pack(a.name)  # validate
        if a.name not in packs:
            packs.append(a.name)
    elif a.action == "rm":
        if a.name in packs:
            packs.remove(a.name)
    save_json(global_file(), data)
    print(f"packs: {', '.join(packs) or '(none)'}")


def cmd_policy(a, cfg):
    if not cfg.policy:
        print("no enterprise policy (looked in: RELAY_POLICY, /etc/relay, /Library/Application Support/relay, ~/.config/relay)")
        return
    shown = {k: v for k, v in cfg.policy.items() if k != "connections"}
    shown["connections"] = sorted(cfg.policy.get("connections", {}))
    print(json.dumps(shown, indent=2))


def cmd_config(a, cfg):
    if a.action == "path":
        cli_path = Path(__file__).resolve().parents[2] / "bin" / "relay"
        print(f"cli:     {cli_path}\nglobal:  {global_file()}\nproject: {project_file()}\naudit:   {audit_file()}")
        if cfg.policy:
            print(f"policy:  {cfg.policy['_path']}")
        return
    print(json.dumps({"mode": cfg.mode, "settings": cfg.settings, "roles": cfg.roles,
                      "connections": sorted(cfg.connections), "tasks": sorted(cfg.tasks)}, indent=2))


# ---------------------------------------------------------------- init

def _ollama_models(host="http://localhost:11434"):
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=2) as r:
            return [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def cmd_init(a, cfg):
    _require_unlocked(cfg, "init")
    pre = presets()
    data = _global()
    conns = data.setdefault("connections", {})
    added = []

    def add(name, preset, **over):
        if name in conns:
            return
        c = {k: v for k, v in pre[preset].items() if k not in ("note", "experimental")}
        c["preset"] = preset
        c.update(over)
        conns[name] = c
        added.append(f"{name} (data={c['data']})")

    if shutil.which("codex"):
        add("codex", "codex")
    if shutil.which("gemini"):
        add("gemini", "gemini")
    models = _ollama_models()
    if models is not None:
        add("ollama-cloud", "ollama-cloud")
        local = [m for m in models if m and not m.endswith(("cloud",))]
        if local:
            add("ollama-local", "ollama-local", model=local[0])
    if os.environ.get("DEEPSEEK_API_KEY"):
        add("deepseek", "deepseek")
    if os.environ.get("OPENROUTER_API_KEY"):
        add("openrouter", "openrouter")
    roles = data.setdefault("roles", {})
    for r, chain in ROLE_DEFAULTS.items():
        roles.setdefault(r, chain)
    if a.mode:
        data["mode"] = a.mode
    data.setdefault("mode", "public")
    packs = data.setdefault("packs", [])
    if a.pack and a.pack not in packs:
        load_pack(a.pack)
        packs.append(a.pack)
    save_json(global_file(), data)
    print(f"relay initialised in {global_file()} (mode={data['mode']}, packs={','.join(packs) or 'none'})")
    print("added: " + (", ".join(added) if added else "nothing new detected"))
    if not os.environ.get("DEEPSEEK_API_KEY") and "deepseek" not in conns:
        print("tip: export DEEPSEEK_API_KEY=... then `relay connect add deepseek` (cheap public-mode default)")
    unverified = [n for n, c in conns.items() if c.get("data") == "unverified"]
    if unverified:
        print("private mode skips these until you confirm their data terms: " + ", ".join(unverified))
        print("  e.g. `relay connect set codex data=no-train` for a business/enterprise account")
    print("next: `relay doctor`, then `relay tasks`")


# ---------------------------------------------------------------- doctor / stats / audit

def cmd_doctor(a, cfg):
    print(f"relay {__version__}  mode={cfg.mode} ({cfg.mode_source()})  allowed data: {', '.join(cfg.allowed_data())}")
    if cfg.policy:
        print(f"policy: {cfg.policy['_path']}{'  [locked]' if cfg.locked() else ''}")
    if not cfg.connections:
        print("no connections: run `relay init`")
    for n, c in sorted(cfg.connections.items()):
        ok, why = backends.availability(n, c)
        allowed, reason = cfg.connection_allowed(n)
        line = f"{'OK ' if ok else 'XX '} {n:<16} {why}"
        if not allowed:
            line += f"  [{reason}]"
        if ok and a.probe:
            t0 = time.time()
            try:
                if c["type"] == "cli":
                    backends.call_cli(n, c, "read", "Reply with the single word OK. Do not use tools.", cfg.settings)
                else:
                    backends.call_api(n, c, [{"role": "user", "content": "Reply with the single word OK."}],
                                      dict(cfg.settings, max_tokens=256))
                line += f"  probe ok {time.time() - t0:.1f}s"
            except RelayError as e:
                line += f"  probe FAILED: {e}"
        print(line)
    print("routes:")
    for name in sorted(cfg.tasks):
        cands, _ = engine.resolve(cfg, name)
        print(f"  {name:<20} -> {' -> '.join(c[0] for c in cands)}")


def _audit_rows(days):
    f = audit_file()
    if not f.exists():
        return []
    cutoff = time.time() - days * 86400
    rows = []
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("ts", 0) >= cutoff:
            rows.append(r)
    return rows


def cmd_stats(a, cfg):
    rows = _audit_rows(a.days)
    if not rows:
        print("no usage yet")
        return
    by, saved, selfs, falls = {}, 0, 0, 0
    for r in rows:
        if r.get("conn") == engine.SELF:
            selfs += 1
            continue
        s = by.setdefault(r.get("conn"), {"ok": 0, "fail": 0, "tok": 0})
        if r.get("ok"):
            s["ok"] += 1
            if r.get("kind") != "verify":
                saved += ((r.get("chars_in") or 0) + (r.get("chars_out") or 0)) // 4
        else:
            s["fail"] += 1
            falls += 1
        s["tok"] += (r.get("tok_in") or 0) + (r.get("tok_out") or 0)
    print(f"last {a.days} days")
    for conn, s in sorted(by.items(), key=lambda kv: -kv[1]["ok"]):
        print(f"  {conn:<34} ok={s['ok']:<4} failed={s['fail']:<3} their_tokens={s['tok']}")
    print(f"  kept with Claude (self): {selfs}   fallbacks taken: {falls}")
    print(f"  ~{saved:,} tokens of reading/writing kept out of Claude's context (chars/4 estimate)")


def cmd_audit(a, cfg):
    rows = _audit_rows(a.days)
    if a.json:
        for r in rows:
            print(json.dumps(r))
        return
    for r in rows:
        t = time.strftime("%Y-%m-%d %H:%M", time.localtime(r.get("ts", 0)))
        files = len(r.get("files") or [])
        status = "ok" if r.get("ok") else "FAIL"
        print(f"{t} {r.get('mode', ''):<7} {r.get('task', ''):<16} -> {r.get('conn', ''):<24} "
              f"data={r.get('data', '-'):<10} files={files:<3} {status}")


# ---------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(prog="relay", description="Route grunt work from Claude Code to the models and accounts you choose.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="run a task through its route")
    s.add_argument("task")
    s.add_argument("instruction")
    s.add_argument("--paths", nargs="+", help="files/dirs to read (read) or reference (write)")
    s.add_argument("--files", nargs="+", help="files to change (edit)")
    s.add_argument("--target", help="new file to create (write)")
    s.add_argument("--via", help="force a connection (still subject to mode/policy)")
    s.add_argument("--verify", action="store_true", help="have the @verifier check the result")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("tasks", help="list tasks and where they route")
    s.add_argument("--brief", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_tasks)

    s = sub.add_parser("task", help="define or remove a task")
    s.add_argument("action", choices=["set", "rm"])
    s.add_argument("name")
    s.add_argument("--route", help="comma list: connection, connection:model, @role, self")
    s.add_argument("--kind", choices=TASK_KINDS)
    s.add_argument("--desc")
    s.add_argument("--verify", help="verifier connection or @role; 'off' to clear")
    s.add_argument("--project", action="store_true", help="save in this repo's .relay.json")
    s.set_defaults(fn=cmd_task)

    s = sub.add_parser("role", help="list | set <role> <chain>")
    s.add_argument("action", choices=["list", "set"])
    s.add_argument("name", nargs="?")
    s.add_argument("chain", nargs="?")
    s.add_argument("--project", action="store_true")
    s.set_defaults(fn=cmd_role)

    s = sub.add_parser("route", help="explain how a task routes right now")
    s.add_argument("task")
    s.add_argument("--via")
    s.set_defaults(fn=cmd_route)

    s = sub.add_parser("connect", help="presets | list | add <preset> | set <name> k=v | rm <name>")
    s.add_argument("action", choices=["presets", "list", "add", "set", "rm"])
    s.add_argument("preset", nargs="?", help="preset (add) or connection name (set/rm)")
    s.add_argument("pairs", nargs="*")
    s.add_argument("--name")
    s.add_argument("--model")
    s.add_argument("--data", choices=DATA_CLASSES)
    s.add_argument("--cost", choices=list(COST_RANK))
    s.add_argument("--bin")
    s.add_argument("--base-url")
    s.add_argument("--key-env")
    s.add_argument("--kinds", help="comma list of read,write,edit")
    s.add_argument("--env", nargs="*", help="KEY=VALUE, e.g. CODEX_HOME=~/.codex-work")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_connect)

    s = sub.add_parser("mode", help="show or set public/private")
    s.add_argument("mode", nargs="?", choices=MODES)
    s.add_argument("--project", action="store_true")
    s.set_defaults(fn=cmd_mode)

    s = sub.add_parser("pack", help="list | add <pack> | rm <pack>")
    s.add_argument("action", choices=["list", "add", "rm"])
    s.add_argument("name", nargs="?")
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("init", help="detect installed CLIs/keys and create a config")
    s.add_argument("--mode", choices=MODES)
    s.add_argument("--pack", default="fullstack")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("doctor", help="check connections and routes")
    s.add_argument("--probe", action="store_true", help="send a tiny prompt to each connection")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("stats", help="usage and savings")
    s.add_argument("--days", type=int, default=7)
    s.set_defaults(fn=cmd_stats)

    s = sub.add_parser("audit", help="what was sent where")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_audit)

    s = sub.add_parser("policy", help="show enterprise policy")
    s.set_defaults(fn=cmd_policy)

    s = sub.add_parser("config", help="show | path")
    s.add_argument("action", choices=["show", "path"], nargs="?", default="show")
    s.set_defaults(fn=cmd_config)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if a.cmd == "role" and a.action == "set" and not (a.name and a.chain):
            raise RelayError("usage: relay role set <role> <conn1,conn2,...>")
        if a.cmd == "pack" and a.action in ("add", "rm") and not a.name:
            raise RelayError("usage: relay pack add|rm <name>")
        if a.cmd == "connect" and a.action in ("add", "set", "rm") and not a.preset:
            raise RelayError("usage: relay connect add <preset> | set <name> k=v | rm <name>")
        cfg = Config()
        a.fn(a, cfg)
    except RelayError as e:
        print(f"relay error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 0
