"""Layered configuration for relay.

Layers, lowest to highest:
  built-in defaults -> packs -> global (~/.config/relay/config.json)
  -> project (.relay.json) -> enterprise policy (enforced)

Security rule: only the global config and the admin policy may define
connections (they can run commands and hold credentials). A project file
can pick the mode, tasks, roles and narrow which connections are allowed,
but it can never add a connection or environment variables.
"""
import json
import os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = PLUGIN_ROOT / "packs"
PRESETS_FILE = Path(__file__).resolve().parent / "presets.json"

DATA_CLASSES = ["local", "no-train", "unverified", "may-train"]
COST_RANK = {"free": 0, "cheap": 1, "subscription": 2, "paid": 3}
MODES = ["public", "private"]
TASK_KINDS = ["read", "write", "edit"]

DEFAULT_SETTINGS = {
    "max_output_chars": 6000,
    "max_corpus_chars": 400000,
    "max_tokens": 8192,
    "timeout_sec": 900,
    "edit_max_lines": 400,
    "extend_pool": True,
    "private_allow": ["local", "no-train"],
    "never_send": [".env", ".env.*", "*.pem", "*.key", "*.pfx", "*.p12", "id_rsa*", "id_ed25519*",
                   "*.kdbx", "credentials*", "secrets.*", "*.tfstate", "*.tfvars"],
    "ignore": [".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__",
               "bin", "obj", ".next", "coverage", "*.lock", "*.min.js", "*.map"],
}

# Generic tasks that always exist. Packs add more specific ones.
BUILTIN_TASKS = {
    "read": {"kind": "read", "route": ["@reader"], "desc": "answer a question from many/large files"},
    "write": {"kind": "write", "route": ["@agent", "@cheap"], "desc": "generate one new file from a spec"},
    "edit": {"kind": "edit", "route": ["@agent", "@cheap"], "desc": "small scoped change to existing files"},
}

PROJECT_KEYS = {"mode", "tasks", "roles", "allow", "deny", "settings"}
PROJECT_SETTING_KEYS = {"max_output_chars", "max_corpus_chars", "edit_max_lines", "extend_pool", "ignore"}


class RelayError(Exception):
    pass


def relay_home():
    return Path(os.environ.get("RELAY_HOME", Path.home() / ".config" / "relay"))


def global_file():
    return relay_home() / "config.json"


def audit_file():
    return relay_home() / "audit.jsonl"


def policy_paths():
    env = os.environ.get("RELAY_POLICY")
    if env:
        return [Path(env)]
    return [Path("/etc/relay/policy.json"),
            Path("/Library/Application Support/relay/policy.json"),
            relay_home() / "policy.json"]


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise RelayError(f"bad JSON in {path}: {e}")


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def project_root(start=None):
    cur = Path(start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        if (d / ".relay.json").exists() or (d / ".git").exists():
            return d
    return cur


def project_file():
    return project_root() / ".relay.json"


def presets():
    return load_json(PRESETS_FILE)


def list_packs():
    return sorted(p.stem for p in PACKS_DIR.glob("*.json"))


def load_pack(name_or_path):
    p = Path(name_or_path)
    if not p.suffix:
        p = PACKS_DIR / f"{name_or_path}.json"
    if not p.exists():
        raise RelayError(f"pack not found: {name_or_path} (built-in: {', '.join(list_packs())})")
    data = load_json(p)
    if "connections" in data:
        raise RelayError(f"pack {p} defines connections; packs may only define tasks and roles")
    return data


def find_policy():
    for p in policy_paths():
        if p.exists():
            data = load_json(p)
            data["_path"] = str(p)
            return data
    return {}


class Config:
    """Effective configuration after merging every layer."""

    def __init__(self):
        self.global_raw = load_json(global_file())
        pf = project_file()
        self.project_path = pf if pf.exists() else None
        self.project_raw = load_json(pf) if pf.exists() else {}
        self.policy = find_policy()
        self._validate_project()

        self.settings = dict(DEFAULT_SETTINGS)
        self.settings.update(self.global_raw.get("settings", {}))
        self.settings.update({k: v for k, v in self.project_raw.get("settings", {}).items()
                              if k in PROJECT_SETTING_KEYS})

        self.connections = dict(self.global_raw.get("connections", {}))
        # admin-provided connections win and cannot be redefined by the user
        self.connections.update(self.policy.get("connections", {}))

        self.roles = {}
        self.tasks = {k: dict(v) for k, v in BUILTIN_TASKS.items()}
        for pack in self.global_raw.get("packs", []):
            data = load_pack(pack)
            for r, chain in data.get("roles", {}).items():
                self.roles.setdefault(r, chain)
            self.tasks.update(data.get("tasks", {}))
        self.roles.update(self.global_raw.get("roles", {}))
        self.roles.update(self.project_raw.get("roles", {}))
        self.tasks.update(self.global_raw.get("tasks", {}))
        self.tasks.update(self.project_raw.get("tasks", {}))
        self.tasks.update(self.policy.get("tasks", {}))

        self.mode = (self.policy.get("force_mode") or self.project_raw.get("mode")
                     or self.global_raw.get("mode") or "public")
        if self.mode not in MODES:
            raise RelayError(f"mode must be one of {MODES}, got {self.mode}")

    def _validate_project(self):
        bad = set(self.project_raw) - PROJECT_KEYS
        if bad:
            raise RelayError(
                f"{self.project_path}: keys {sorted(bad)} are not allowed in a project file "
                f"(allowed: {sorted(PROJECT_KEYS)}). Connections live only in your global config.")

    # ---- policy

    def mode_source(self):
        if self.policy.get("force_mode"):
            return f"policy {self.policy['_path']}"
        if self.project_raw.get("mode"):
            return f"project {self.project_path}"
        if self.global_raw.get("mode"):
            return "global config"
        return "default"

    def allowed_data(self):
        if self.mode == "public":
            allowed = list(DATA_CLASSES)
        else:
            allowed = list(self.settings.get("private_allow", ["local", "no-train"]))
        if "allow_data" in self.policy:
            allowed = [d for d in allowed if d in self.policy["allow_data"]]
        return allowed

    def connection_allowed(self, name):
        """Return (ok, reason)."""
        conn = self.connections.get(name)
        if conn is None:
            return False, "not defined"
        data = conn.get("data", "unverified")
        if data not in self.allowed_data():
            return False, f"data={data} not allowed in {self.mode} mode"
        pol = self.policy.get("allow_connections")
        if pol is not None and name not in pol:
            return False, "blocked by enterprise policy"
        if name in self.policy.get("deny_connections", []):
            return False, "blocked by enterprise policy"
        proj_allow = self.project_raw.get("allow")
        if proj_allow is not None and name not in proj_allow:
            return False, "not in this project's allow list"
        if name in self.project_raw.get("deny", []):
            return False, "denied by this project"
        return True, ""

    def locked(self):
        return bool(self.policy.get("lock"))
