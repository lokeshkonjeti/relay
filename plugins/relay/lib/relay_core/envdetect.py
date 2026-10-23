"""Work out how a project runs its tests, so delegates use the right interpreter and commands.

Only file names, lockfiles, package.json scripts and the *host* part of service URLs are looked
at. Values from .env files (passwords, keys) are never returned or sent anywhere.
"""
import json
import os
import re
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build",
             ".next", "coverage", "target", "bin", "obj", ".idea", ".vscode"}
VENV_NAMES = (".venv", "venv", "env")
NPM_DEFAULT_TEST = "no test specified"
LOCAL_HOST = re.compile(r"(?:@|//|=\s*)(localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal)\b", re.I)
SERVICE_KEY = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]*(?:URL|URI|HOST|DSN|CONNECTION_STRING))\s*=", re.I)
SERVICE_WORDS = ("postgres", "mysql", "mariadb", "mongo", "redis", "sqlserver", "mssql", "rabbit",
                 "kafka", "elastic", "database", "db")


def _rel(root, p):
    try:
        return str(Path(p).relative_to(root)) or "."
    except ValueError:
        return str(p)


def _dirs(root):
    """The repo root plus its immediate subfolders (backend/, api/, web/, frontend/ ...)."""
    out = [root]
    try:
        out += sorted(d for d in root.iterdir() if d.is_dir() and d.name not in SKIP_DIRS
                      and not d.name.startswith("."))
    except OSError:
        pass
    return out


def _venv_python(d):
    for name in VENV_NAMES:
        for exe in ("bin/python", "Scripts/python.exe"):
            p = d / name / exe
            if p.exists():
                return p
    return None


def _has_pytest(d, root):
    for f in ("pytest.ini", "conftest.py", "tox.ini", "setup.cfg"):
        if (d / f).exists():
            return True
    pp = d / "pyproject.toml"
    if pp.exists():
        try:
            text = pp.read_text(errors="replace")
            if "pytest" in text:
                return True
        except OSError:
            pass
    for t in (d / "tests", d / "test"):
        if t.is_dir() and any(t.glob("test_*.py")) or (t.is_dir() and any(t.glob("*_test.py"))):
            return True
    return False


def _python(root, d):
    tests = next((t for t in (d / "tests", d / "test") if t.is_dir()), None)
    if not _has_pytest(d, root) and tests is None:
        return None
    target = _rel(root, tests) if tests else _rel(root, d)
    py = _venv_python(d) or (_venv_python(root) if d != root else None)
    if py:
        return f"{_rel(root, py)} -m pytest {target} -q", f"Python: use {_rel(root, py)} (the project venv), not the system python"
    prefix = "" if d == root else f"cd {_rel(root, d)} && "
    if (d / "uv.lock").exists():
        return f"{prefix}uv run pytest -q", "Python: dependencies are managed by uv"
    if (d / "poetry.lock").exists():
        return f"{prefix}poetry run pytest -q", "Python: dependencies are managed by Poetry"
    if (d / "Pipfile.lock").exists():
        return f"{prefix}pipenv run pytest -q", "Python: dependencies are managed by Pipenv"
    return f"python -m pytest {target} -q", None


def _node(root, d):
    pj = d / "package.json"
    if not pj.exists():
        return None
    try:
        scripts = (json.loads(pj.read_text(errors="replace")).get("scripts") or {})
    except (OSError, json.JSONDecodeError):
        return None
    test = scripts.get("test", "")
    if not test or NPM_DEFAULT_TEST in test:
        return None
    pm = "npm"
    for lock, name in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lockb", "bun"),
                       ("bun.lock", "bun")):
        if (d / lock).exists() or (root / lock).exists():
            pm = name
            break
    prefix = "" if d == root else f"cd {_rel(root, d)} && "
    cmd = f"{prefix}{pm} test" if pm != "npm" else f"{prefix}npm test --silent"
    extras = [s for s in ("typecheck", "lint") if s in scripts]
    note = f"JS/TS: use {pm}" + (f"; also available: {', '.join(f'{pm} run {s}' for s in extras)}" if extras else "")
    return cmd, note


def _jvm_go_dotnet(root, d):
    prefix = "" if d == root else f"cd {_rel(root, d)} && "
    if (d / "mvnw").exists():
        return f"{prefix}./mvnw -q test", None
    if (d / "pom.xml").exists():
        return f"{prefix}mvn -q test", None
    if (d / "gradlew").exists():
        return f"{prefix}./gradlew test", None
    if (d / "go.mod").exists():
        return f"{prefix}go test ./...", None
    if any(d.glob("*.sln")) or any(d.glob("*.csproj")):
        return f"{prefix}dotnet test", None
    return None


def _local_services(root):
    """True if the project's config points services (DB, cache, queue) at localhost."""
    files = []
    for d in _dirs(root):
        for name in (".env", ".env.local", ".env.development", ".env.test", ".env.example"):
            if (d / name).is_file():
                files.append(d / name)
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
            if (d / name).is_file():
                return True  # services defined for local runs
    for f in files:
        try:
            for line in f.read_text(errors="replace").splitlines()[:400]:
                if line.lstrip().startswith("#"):
                    continue
                m = SERVICE_KEY.match(line)
                low = line.lower()
                if (m or any(w in low for w in SERVICE_WORDS)) and LOCAL_HOST.search(line):
                    return True
        except OSError:
            continue
    return False


def detect(root, overrides=None):
    """Return {"tests": [cmd...], "notes": [...], "local_services": bool}."""
    root = Path(root).resolve()
    tests, notes = [], []
    for d in _dirs(root):
        for finder in (_python, _node, _jvm_go_dotnet):
            try:
                found = finder(root, d) if finder is not _jvm_go_dotnet else _jvm_go_dotnet(root, d)
            except OSError:
                found = None
            if found:
                cmd, note = found
                if cmd not in tests:
                    tests.append(cmd)
                if note and note not in notes:
                    notes.append(note)
    env = {"tests": tests[:4], "notes": notes[:4], "local_services": _local_services(root)}
    if overrides:
        if overrides.get("test"):
            env["tests"] = [overrides["test"]]
        if "local_services" in overrides:
            env["local_services"] = bool(overrides["local_services"])
        if overrides.get("note"):
            env["notes"] = [overrides["note"]] + env["notes"]
    return env


def prompt_block(env):
    """Short instructions for an agent CLI. Empty when nothing was detected."""
    lines = []
    if env.get("tests"):
        lines.append("Run tests with: " + " ; ".join(f"`{c}`" for c in env["tests"]) + " (from the repo root).")
    lines += env.get("notes", [])
    if env.get("local_services"):
        lines.append("Tests use local services (database etc.) on localhost; they are expected to be running.")
    if not lines:
        return ""
    lines.append("Use the project's own interpreter and tools; do not install packages.")
    return "Project environment (detected by relay):\n- " + "\n- ".join(lines) + "\n"


TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.(py|go)|[^/]*\.(test|spec)\.[jt]sx?|[^/]*Tests?\.(java|kt|cs))$")


def is_test_path(p):
    p = str(p).replace(os.sep, "/")
    return bool(TEST_FILE.search(p)) or "/tests/" in f"/{p}" or "/__tests__/" in f"/{p}"
