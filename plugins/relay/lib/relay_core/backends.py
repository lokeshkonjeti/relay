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

def build_cli_cmd(conn, kind, prompt, out_path, model=None):
    model = model or conn.get("model")
    variables = {"prompt": prompt, "out": str(out_path), "model": model or ""}
    variables.update((conn.get("mode_vars") or {}).get(kind, {}))
    model_args = [a.replace("{model}", model) for a in conn.get("model_args", [])] if model else []
    mode_args = list((conn.get("mode_args") or {}).get(kind, []))
    cmd = [_expand(conn["bin"])]
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


def call_cli(name, conn, kind, prompt, settings, model=None, cwd=None):
    exe = find_bin(conn.get("bin"))
    if not exe:
        raise RelayError(f"{name}: `{conn.get('bin')}` not found")
    tmo = timeout_for(conn, settings)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "last.txt"
        cmd = build_cli_cmd(conn, kind, prompt, out, model)
        cmd[0] = exe
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=tmo,
                               stdin=subprocess.DEVNULL, env=conn_env(conn), cwd=cwd)
        except subprocess.TimeoutExpired:
            raise RelayError(f"{name}: timed out after {tmo}s")
        if conn.get("output") == "file":
            text = out.read_text() if out.exists() else ""
        else:
            text = p.stdout
    if p.returncode != 0 and not text.strip():
        tail = " | ".join((p.stderr or p.stdout or "").strip().splitlines()[-3:])
        low = tail.lower()
        hint = " (quota/limit hit)" if any(w in low for w in ("limit", "quota", "429", "rate")) else ""
        raise RelayError(f"{name}: exit {p.returncode}{hint}: {tail[:300]}")
    if not text.strip():
        raise RelayError(f"{name}: no output")
    return text, 0, 0
