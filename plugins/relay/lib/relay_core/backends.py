"""Talk to the two kinds of connection: OpenAI-compatible APIs and agent CLIs."""
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .config import RelayError


def _expand(v):
    return os.path.expandvars(os.path.expanduser(str(v)))


EXTRA_BIN_DIRS = ["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "~/.npm-global/bin",
                  "~/.bun/bin", "~/.volta/bin", "~/.cargo/bin", "~/bin"]


def find_bin(b):
    """shutil.which, then common install dirs that a GUI-launched app's PATH often misses."""
    if not b:
        return None
    b = _expand(b)
    found = shutil.which(b)
    if found or os.sep in b:
        return found
    dirs = [os.path.expanduser(d) for d in EXTRA_BIN_DIRS]
    nvm = Path(os.path.expanduser("~/.nvm/versions/node"))
    if nvm.is_dir():
        dirs += [str(v / "bin") for v in sorted(nvm.iterdir(), reverse=True)]
    for d in dirs:
        cand = Path(d) / b
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def timeout_for(conn, settings):
    return int(conn.get("timeout_sec") or settings["timeout_sec"])


def conn_env(conn):
    env = dict(os.environ)
    for k, v in (conn.get("env") or {}).items():
        env[k] = _expand(v)
    env["RELAY_DEPTH"] = str(int(os.environ.get("RELAY_DEPTH", "0") or 0) + 1)
    return env


def availability(name, conn):
    """Cheap check with no model call. Returns (ok, detail)."""
    if conn.get("type") == "cli":
        b = conn.get("bin")
        if not b:
            return False, "no bin set"
        path = find_bin(b)
        if path:
            return True, path
        return False, (f"`{b}` not found. If it is installed, restart Claude Code (it keeps the PATH "
                       f"from when it started) or set the full path: relay connect set {name} bin=$(which {b})")
    base = conn.get("base_url")
    if not base:
        return False, "no base_url set"
    if conn.get("auth", "bearer") != "none":
        key_env = conn.get("key_env")
        if not key_env or not os.environ.get(key_env):
            return False, f"env {key_env or '<key_env>'} not set"
    if not conn.get("model"):
        return False, "no model set"
    return True, base


# ---------------------------------------------------------------- API

def call_api(name, conn, messages, settings, model=None):
    base = _expand(conn.get("base_url", "")).rstrip("/")
    model = model or conn.get("model")
    if not base or not model:
        raise RelayError(f"{name}: base_url and model are required")
    headers = {"Content-Type": "application/json"}
    auth = conn.get("auth", "bearer")
    if auth != "none":
        key = os.environ.get(conn.get("key_env") or "")
        if not key:
            raise RelayError(f"{name}: env {conn.get('key_env')} is not set")
        if auth == "api-key":
            headers["api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    for k, v in (conn.get("headers") or {}).items():
        headers[k] = _expand(v)
    body = {"model": model, "messages": messages, "stream": False,
            "max_tokens": conn.get("max_tokens", settings["max_tokens"])}
    body.update(conn.get("extra_body") or {})
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout_for(conn, settings)) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200].replace("\n", " ")
        raise RelayError(f"{name}: HTTP {e.code} {detail}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise RelayError(f"{name}: unreachable ({e})")
    except json.JSONDecodeError:
        raise RelayError(f"{name}: non-JSON response")
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    if not text.strip():
        raise RelayError(f"{name}: empty answer (thinking models may need a higher max_tokens)")
    usage = data.get("usage") or {}
    return text, usage.get("prompt_tokens", 0) or 0, usage.get("completion_tokens", 0) or 0


# ---------------------------------------------------------------- CLI

# Sandbox network for agent CLIs, applied to write/edit runs only (reads never need it).
#   off   : the CLI's default sandbox (Codex: no network at all)
#   local : localhost only, e.g. a dev Postgres/Redis/API for running tests (Codex network proxy)
#   on    : unrestricted outbound network
NETWORK_ARGS = {
    "codex": {
        "local": ["-c", "sandbox_workspace_write.network_access=true",
                  "-c", "features.network_proxy.enabled=true",
                  "-c", 'features.network_proxy.domains={ "localhost" = "allow", "127.0.0.1" = "allow" }'],
        "on": ["-c", "sandbox_workspace_write.network_access=true"],
    },
}
NETWORK_MODES = ["off", "local", "on"]


def _preset_of(conn):
    return conn.get("preset") or Path(str(conn.get("bin", ""))).name


def extra_cli_args(conn, kind):
    """Args inserted right after the binary, so they also work with configs saved by older versions."""
    out = []
    net = conn.get("network", "off")
    if kind in ("write", "edit") and net != "off":
        table = NETWORK_ARGS.get(_preset_of(conn))
        if not table:
            raise RelayError(f"network={net} is not supported for {_preset_of(conn)}; use extra_args")
        out += table[net]
    extra = conn.get("extra_args") or []
    if isinstance(extra, dict):
        extra = extra.get(kind, [])
    return out + [_expand(a) for a in extra]


def build_cli_cmd(conn, kind, prompt, out_path, model=None):
    model = model or conn.get("model")
    variables = {"prompt": prompt, "out": str(out_path), "model": model or ""}
    variables.update((conn.get("mode_vars") or {}).get(kind, {}))
    model_args = [a.replace("{model}", model) for a in conn.get("model_args", [])] if model else []
    mode_args = list((conn.get("mode_args") or {}).get(kind, []))
    cmd = [_expand(conn["bin"])] + extra_cli_args(conn, kind)
    for a in conn.get("args", ["{prompt}"]):
        if a == "{model_args}":
            cmd += model_args
        elif a == "{mode_args}":
            cmd += mode_args
        else:
            for k, v in variables.items():
                a = a.replace("{" + k + "}", str(v))
            cmd.append(a)
    return cmd


def _codex_usage(stdout):
    """Sum token usage from `codex exec --json` events. Returns (input, cached_input, output)."""
    tin = tcached = tout = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{") or "usage" not in line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "turn.completed":
            u = ev.get("usage") or {}
            tin += u.get("input_tokens", 0) or 0
            tcached += u.get("cached_input_tokens", 0) or 0
            tout += u.get("output_tokens", 0) or 0
    return tin, tcached, tout


def _wants_json_usage(conn):
    return (_preset_of(conn) == "codex" and conn.get("output") == "file"
            and conn.get("usage_json", True))


def _with_json_flag(cmd):
    """Insert --json right after the `exec` subcommand (it is not a global flag)."""
    if "exec" in cmd[1:]:
        i = cmd.index("exec", 1)
        return cmd[:i + 1] + ["--json"] + cmd[i + 1:]
    return cmd


def call_cli(name, conn, kind, prompt, settings, model=None, cwd=None):
    """Run an agent CLI. Returns (text, input_tokens, output_tokens); tokens are 0 when unknown.
    Cached input tokens (Codex) are stored in conn-independent form via the `usage` attribute."""
    exe = find_bin(conn.get("bin"))
    if not exe:
        raise RelayError(f"{name}: `{conn.get('bin')}` not found")
    tmo = timeout_for(conn, settings)
    json_usage = _wants_json_usage(conn)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "last.txt"
        base = build_cli_cmd(conn, kind, prompt, out, model)
        base[0] = exe
        attempts = [_with_json_flag(base), base] if json_usage else [base]
        for i, cmd in enumerate(attempts):
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=tmo,
                                   stdin=subprocess.DEVNULL, env=conn_env(conn), cwd=cwd)
            except subprocess.TimeoutExpired:
                raise RelayError(f"{name}: timed out after {tmo}s")
            unknown_flag = ("--json" in (p.stderr or "") and
                            any(w in (p.stderr or "").lower() for w in ("unexpected", "unrecognized", "unknown")))
            if p.returncode != 0 and unknown_flag and i == 0 and len(attempts) > 1:
                json_usage = False
                continue  # an older CLI without --json: run again plainly
            break
        if conn.get("output") == "file":
            text = out.read_text() if out.exists() else ""
        else:
            text = p.stdout
    if p.returncode != 0 and not text.strip():
        tail = " | ".join((p.stderr or "").strip().splitlines()[-3:]) or (p.stdout or "").strip()[-300:]
        low = tail.lower()
        hint = " (quota/limit hit)" if any(w in low for w in ("limit", "quota", "429", "rate")) else ""
        raise RelayError(f"{name}: exit {p.returncode}{hint}: {tail[:300]}")
    if not text.strip():
        raise RelayError(f"{name}: no output")
    tin = tcached = tout = 0
    if json_usage:
        tin, tcached, tout = _codex_usage(p.stdout)
    LAST_USAGE.clear()
    LAST_USAGE.update(input=tin, cached=tcached, output=tout)
    return text, tin, tout


LAST_USAGE = {}
