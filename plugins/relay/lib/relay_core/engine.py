"""Route a task to a connection and run it, falling back down the chain."""
import difflib
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import backends, envdetect
from .config import COST_RANK, RelayError, audit_file, project_root, relay_home

SELF = "self"

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".jar", ".class", ".exe",
              ".dll", ".so", ".dylib", ".ico", ".woff", ".woff2", ".ttf", ".mp4", ".mp3", ".onnx",
              ".pt", ".bin", ".parquet", ".pkl", ".sqlite", ".db"}


# ---------------------------------------------------------------- routing

def parse_ref(ref):
    if ref == SELF:
        return SELF, None
    name, _, model = ref.partition(":")
    return name, (model or None)


def expand_route(cfg, route, seen=None):
    seen = seen or set()
    out = []
    for item in route:
        if item.startswith("@"):
            role = item[1:]
            if role in seen:
                continue
            out += expand_route(cfg, cfg.roles.get(role, []), seen | {role})
        else:
            out.append(item)
    return out


def resolve(cfg, task_name, via=None):
    """Return (candidates, skipped). candidates: list of (name, model); SELF may appear."""
    task = cfg.tasks.get(task_name)
    if task is None:
        raise RelayError(f"unknown task '{task_name}'. Known: {', '.join(sorted(cfg.tasks))}")
    kind = task.get("kind", "read")
    route = [via] if via else expand_route(cfg, task.get("route", []))
    explicit = {via} if via else {r for r in task.get("route", []) if not r.startswith("@")}
    cands, skipped, seen = [], [], set()
    expanded = []
    for ref in route:
        name, model = parse_ref(ref)
        if name != SELF and name not in cfg.connections:
            # a preset name (e.g. "codex") matches every account built from it (codex-work, codex-personal)
            alts = sorted(n for n, c in cfg.connections.items() if c.get("preset") == name)
            if alts:
                expanded += [n + (f":{model}" if model else "") for n in alts]
                explicit |= set(alts) if ref in explicit else set()
                continue
        expanded.append(ref)
    for ref in expanded:
        name, model = parse_ref(ref)
        if name == SELF:
            cands.append((SELF, None))
            break  # anything after self is unreachable
        key = (name, model)
        if key in seen:
            continue
        seen.add(key)
        ok, why = usable(cfg, name, kind)
        if ok:
            cands.append(key)
        elif name in cfg.connections or ref in explicit:
            skipped.append(f"{name}: {why}")  # role entries you never set up stay silent
    if via is None and cfg.settings.get("extend_pool", True) and (not cands or cands[-1][0] != SELF):
        used = {c[0] for c in cands} | {s.split(":")[0] for s in skipped}
        pool = []
        for name, conn in cfg.connections.items():
            if name in used:
                continue
            ok, _ = usable(cfg, name, kind)
            if ok:
                pool.append((COST_RANK.get(conn.get("cost", "paid"), 3), name))
        cands += [(n, None) for _, n in sorted(pool)]
    if not cands or cands[-1][0] != SELF:
        cands.append((SELF, None))
    return cands, skipped


def usable(cfg, name, kind):
    conn = cfg.connections.get(name)
    if conn is None:
        return False, "no such connection"
    ok, why = cfg.connection_allowed(name)
    if not ok:
        return False, why
    if kind not in conn.get("kinds", ["read", "write", "edit"]):
        return False, f"not enabled for {kind}"
    if conn.get("disabled"):
        return False, "turned off by `relay up` (failed its test); rerun `relay up` after fixing"
    ok, why = backends.availability(name, conn)
    return ok, ("" if ok else why)


# ---------------------------------------------------------------- helpers

def _ignored(rel, patterns):
    return any(fnmatch.fnmatch(part, pat) for part in rel.parts for pat in patterns)


def collect_files(paths, settings):
    files = []
    for p in paths or []:
        p = Path(p)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and not _ignored(f.relative_to(p), settings["ignore"]):
                    files.append(f)
        elif p.is_file():
            files.append(p)
        else:
            raise RelayError(f"not found: {p}")
    secret = settings.get("never_send", [])
    return [f for f in files if f.suffix.lower() not in BINARY_EXT
            and not any(fnmatch.fnmatch(f.name, pat) for pat in secret)]


def pack_corpus(paths, settings):
    docs, total, used, skipped = [], 0, [], 0
    for f in collect_files(paths, settings):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if "\x00" in text[:2048]:
            continue
        if total + len(text) > settings["max_corpus_chars"]:
            skipped += 1
            continue
        total += len(text)
        used.append(str(f))
        docs.append(f"<file path='{f}'>\n{text}\n</file>")
    return "\n\n".join(docs), total, used, skipped


def referenced_chars(paths, settings, limit=5_000_000):
    """Size of the text files a CLI agent was pointed at (it reads them itself, so relay never sees them)."""
    total = 0
    try:
        for f in collect_files(paths or [], settings):
            total += f.stat().st_size
            if total > limit:
                return limit
    except (RelayError, OSError):
        pass
    return total


def diff_chars(files):
    if not shutil.which("git") or not files:
        return 0
    r = subprocess.run(["git", "diff", "--", *map(str, files)], capture_output=True, text=True)
    return len(r.stdout) if r.returncode == 0 else 0


FENCE = re.compile(r"^\s*(```|~~~)")
MARKDOWN_EXT = {".md", ".mdx", ".markdown"}


def strip_fence(text, target=None):
    """Return the file body from a model reply.

    The reply may wrap the file in an outer fence, and the file itself may contain
    fences (Markdown with Mermaid/code blocks). So unwrap from the FIRST fence line
    to the LAST fence line, never to the first closing fence.
    """
    lines = text.strip("\n").splitlines()
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first is None:
        return text
    fences = [i for i, l in enumerate(lines) if FENCE.match(l)]
    if not fences:
        return text
    if FENCE.match(lines[first]):
        start = first
    elif target and Path(target).suffix.lower() in MARKDOWN_EXT:
        # prose before an outer wrapper (```markdown, ```md, ``` or ````), else the reply is the file
        opener = lines[fences[0]].strip()
        if opener.startswith("````") or opener.lstrip("`~").strip().lower() in ("", "markdown", "md", "mdx"):
            start = fences[0]
        else:
            return text  # unwrapped Markdown whose own code blocks must stay
    else:
        start = fences[0]  # prose before the code block
    end = fences[-1]
    if end <= start:
        return "\n".join(lines[start + 1:]) + "\n"
    return "\n".join(lines[start + 1:end]) + "\n"


def check_body(label, body, target):
    """Reject output that is obviously broken, so the route falls back instead of writing it."""
    if not body.strip():
        raise RelayError(f"{label}: returned an empty file")
    n = sum(1 for l in body.splitlines() if FENCE.match(l))
    if n % 2:
        raise RelayError(f"{label}: returned a file with an unclosed code block (looks cut off); not written")


def cap(text, settings):
    n = settings["max_output_chars"]
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[:n] + f"\n[relay: truncated {len(text) - n} chars; ask a narrower question]"


def _hash(p):
    try:
        return hashlib.sha1(Path(p).read_bytes()).hexdigest()
    except OSError:
        return None


def snapshot(paths):
    snap = {str(p): _hash(p) for p in paths}
    if shutil.which("git"):
        r = subprocess.run(["git", "status", "--porcelain", "-uall"], capture_output=True, text=True)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                path = line[3:].split(" -> ")[-1].strip('"')
                snap.setdefault(path, _hash(path))
    return snap


def changed(before, after_paths):
    after = snapshot(after_paths)
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k, _hash(k)))


def audit(cfg, **rec):
    try:
        f = audit_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        rec.update(ts=round(time.time()), mode=cfg.mode, repo=str(project_root()))
        if "files" in rec:
            rec["files"] = rec["files"][:50]
        with open(f, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- project environment

def project_env(cfg):
    root = project_root()
    over = (cfg.global_raw.get("projects") or {}).get(str(root))
    try:
        return envdetect.detect(root, over)
    except Exception:  # detection must never break a delegation
        return {"tests": [], "notes": [], "local_services": False}


def run_conn(conn, env):
    """Per-run copy of a connection with network 'auto' resolved for this project."""
    c = dict(conn)
    net = c.get("network", "auto")
    if net == "auto":
        supported = backends._preset_of(c) in backends.NETWORK_ARGS
        c["network"] = "local" if (supported and env.get("local_services")) else "off"
    return c


# ---------------------------------------------------------------- prompts

SYS_READ = ("You are a precise code analyst. Answer only from the files given. Cite file paths and "
            "function names. Be concise: bullets, no preamble. If the files do not contain the "
            "answer, say so.")
SYS_WRITE = ("You write complete, working source files that follow the conventions of the reference "
             "files exactly (framework, naming, imports, style). Output ONLY the file content wrapped in "
             "one outer fence of four backticks (````), so code blocks inside the file stay intact. "
             "No explanations.")
SYS_EDIT = ("You edit one source file. Apply exactly the requested change and nothing else. Keep "
            "everything else byte-for-byte identical. Output the COMPLETE updated file in one fenced "
            "code block. No explanations.")


def _task_line(task):
    return f"Task type: {task.get('desc', '')}\n" if task.get("desc") else ""


# ---------------------------------------------------------------- execution

class Result:
    def __init__(self, conn, text, notes, kind):
        self.conn, self.text, self.notes, self.kind = conn, text, notes, kind


def run_task(cfg, task_name, instruction, paths=None, files=None, target=None, force=False,
             via=None, verify=False):
    task = cfg.tasks[task_name] if task_name in cfg.tasks else None
    if task is None:
        raise RelayError(f"unknown task '{task_name}'. Known: {', '.join(sorted(cfg.tasks))}")
    kind = task.get("kind", "read")
    if kind == "read" and not paths:
        raise RelayError("read tasks need --paths")
    if kind == "write" and not target:
        raise RelayError("write tasks need --target")
    if kind == "edit" and not (files or paths):
        raise RelayError("edit tasks need --files")
    if kind == "edit" and not files:
        files = paths
    if target and Path(target).exists() and not force:
        raise RelayError(f"{target} exists; pass --force or pick a new path")
    missing = [p for p in (paths or []) + (files or []) if not Path(p).exists()]
    if missing:
        paths = [p for p in (paths or []) if Path(p).exists()] or None
        files = [p for p in (files or []) if Path(p).exists()] or None
        if kind == "read" and not paths:
            raise RelayError(f"none of the paths exist: {', '.join(missing)}")
        if kind == "edit" and not files:
            raise RelayError(f"none of the files exist: {', '.join(missing)}")

    cands, skipped = resolve(cfg, task_name, via)
    notes = [f"skipped {s}" for s in skipped]
    if missing:
        notes.insert(0, f"ignored paths that don't exist: {', '.join(missing)}")
    s = cfg.settings
    env = project_env(cfg)
    env_text = envdetect.prompt_block(env)

    corpus = used = None
    for name, model in cands:
        if name == SELF:
            reason = "routed to self" if not notes else "no delegate available"
            audit(cfg, task=task_name, conn=SELF, ok=True, kind=kind)
            return Result(SELF, reason, notes, kind)
        conn = cfg.connections[name]
        label = f"{name}:{model}" if model else name
        t0 = time.time()
        sent_files, chars_in, chars_gen = [], 0, 0
        try:
            if conn["type"] == "api":
                if kind == "edit":
                    if len(files) != 1:
                        raise RelayError(f"{label}: API connections edit one file at a time")
                    src = Path(files[0])
                    old = src.read_text()
                    if len(old.splitlines()) > s["edit_max_lines"]:
                        raise RelayError(f"{label}: file over edit_max_lines={s['edit_max_lines']}")
                    msgs = [{"role": "system", "content": SYS_EDIT},
                            {"role": "user", "content": f"<file path='{src}'>\n{old}\n</file>"},
                            {"role": "user", "content": _task_line(task) + f"Change: {instruction}"}]
                    sent_files, chars_in = [str(src)], len(old)
                    text, pin, pout = backends.call_api(name, conn, msgs, s, model)
                    new = strip_fence(text, src).rstrip("\n") + "\n"
                    check_body(label, new, src)
                    if len(old.splitlines()) > 20 and len(new.splitlines()) < 0.5 * len(old.splitlines()):
                        raise RelayError(f"{label}: returned file looks truncated; not applied")
                    src.write_text(new)
                    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
                    plus = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
                    minus = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
                    chars_gen = sum(len(d) for d in diff)
                    out = f"edited {src} (+{plus} -{minus}). Review with `git diff {src}`."
                else:
                    if corpus is None and (paths or (kind == "write" and files)):
                        corpus, chars, used, nskip = pack_corpus(paths or files, s)
                        if nskip:
                            notes.append(f"corpus cap hit: {nskip} files left out")
                    sent_files, chars_in = list(used or []), len(corpus or "")
                    if kind == "read":
                        msgs = [{"role": "system", "content": SYS_READ},
                                {"role": "user", "content": f"<corpus>\n{corpus}\n</corpus>"},
                                {"role": "user", "content": _task_line(task) + instruction}]
                        text, pin, pout = backends.call_api(name, conn, msgs, s, model)
                        out = text
                    else:
                        ref = f"<reference>\n{corpus}\n</reference>" if corpus else "(no reference files)"
                        msgs = [{"role": "system", "content": SYS_WRITE},
                                {"role": "user", "content": ref},
                                {"role": "user", "content": _task_line(task)
                                 + f"Write the file `{target}`.\nSpec: {instruction}"}]
                        text, pin, pout = backends.call_api(name, conn, msgs, s, model)
                        body = strip_fence(text, target).rstrip("\n") + "\n"
                        check_body(label, body, target)
                        Path(target).parent.mkdir(parents=True, exist_ok=True)
                        Path(target).write_text(body)
                        chars_gen = len(body)
                        out = f"wrote {target} ({len(body.splitlines())} lines). Review before relying on it."
            else:  # cli agent
                pin = pout = 0
                rconn = run_conn(conn, env)
                ref_paths = " ".join(map(str, paths or files or []))
                if kind == "read":
                    prompt = (f"{SYS_READ}\n{_task_line(task)}Read these paths: {ref_paths}\n"
                              f"Question: {instruction}\nDo not modify any files.")
                    text, pin, pout = backends.call_cli(name, rconn, kind, prompt, s, model)
                    out = text
                elif kind == "write":
                    before = snapshot([target])
                    run_tests = ""
                    if envdetect.is_test_path(target) and env.get("tests"):
                        run_tests = (f"After writing it, run {target} with the test command above and fix "
                                     f"{target} (only) until it passes, or explain why it cannot. "
                                     "Report the pass/fail counts.\n")
                    prompt = (f"{env_text}{_task_line(task)}Create the file {target}.\nSpec: {instruction}\n"
                              f"Follow the conventions of: {ref_paths or 'the surrounding code'}.\n"
                              f"Only create or modify {target}. Do not run installs.\n{run_tests}"
                              "Finish with one line describing the file" + (" and the test result." if run_tests else "."))
                    text, pin, pout = backends.call_cli(name, rconn, kind, prompt, s, model)
                    if str(target) not in changed(before, [target]) or not Path(target).exists():
                        raise RelayError(f"{label}: did not write {target}")
                    written = Path(target).read_text()
                    n, chars_gen = len(written.splitlines()), len(written)
                    last = text.strip().splitlines()[-1][:200] if text.strip() else ""
                    out = f"wrote {target} ({n} lines). {last}"
                else:
                    before = snapshot(files)
                    prompt = (f"{env_text}{_task_line(task)}{instruction}\nRelevant files: {' '.join(files)}\n"
                              "Keep the change minimal and scoped. Do not refactor unrelated code or "
                              "weaken tests. Do not run installs or long builds. If tests cover this "
                              "code, run them with the test command above.\n"
                              "Finish with: files changed, one line each, and the test result if run.")
                    text, pin, pout = backends.call_cli(name, rconn, kind, prompt, s, model)
                    diff = changed(before, files)
                    if not diff:
                        raise RelayError(f"{label}: made no changes ({text.strip()[:150]})")
                    chars_gen = diff_chars(diff)
                    out = f"{text.strip()}\nchanged: {', '.join(diff)}"
                sent_files = [str(p) for p in (paths or files or [])] + ([str(target)] if target else [])
                chars_in = referenced_chars(paths or files, s)
            audit(cfg, task=task_name, conn=label, data=conn.get("data", "unverified"), ok=True,
                  kind=kind, secs=round(time.time() - t0, 1), files=sent_files,
                  chars_in=chars_in, chars_gen=chars_gen, chars_back=len(out), tok_in=pin, tok_out=pout,
                  tok_cached=backends.LAST_USAGE.get("cached", 0) if conn["type"] == "cli" else 0)
            res = Result(label, out, notes, kind)
            want = verify or bool(task.get("verify"))
            if want:
                _verify(cfg, task, task_name, res, instruction, paths, files, target, exclude=name)
            return res
        except RelayError as e:
            audit(cfg, task=task_name, conn=label, data=conn.get("data"), ok=False, kind=kind,
                  secs=round(time.time() - t0, 1), files=sent_files, error=str(e)[:200])
            notes.append(f"failed {e}")
    raise RelayError("unreachable")  # self is always last


def _verify(cfg, task, task_name, res, instruction, paths, files, target, exclude):
    ref = task.get("verify") or "@verifier"
    route = expand_route(cfg, [ref]) if ref.startswith("@") else [ref]
    for r in route:
        name, model = parse_ref(r)
        if name in (SELF, exclude) or name not in cfg.connections:
            continue
        ok, why = usable(cfg, name, "read")
        if not ok:
            res.notes.append(f"verifier {name} skipped: {why}")
            continue
        conn = cfg.connections[name]
        where = " ".join(map(str, (paths or []) + (files or []) + ([target] if target else [])))
        if res.kind == "read":
            prompt = ("Another model answered a question about files in this repo. Open the files "
                      f"and check every claim.\nPaths: {where}\nQuestion: {instruction}\n"
                      f"--- ANSWER ---\n{res.text}\n--- END ---\nReply with exactly one of:\n"
                      "VERIFIED (then at most 2 short bullets), or\nCORRECTED (then the corrected "
                      "answer, concise bullets).\nDo not modify any files.")
        else:
            prompt = (f"Review the change just made to: {where}\nIntended change: {instruction}\n"
                      "Check correctness, scope, and that nothing unrelated changed. Reply with "
                      "exactly one of:\nOK (one line), or\nISSUES (then bullets).\nDo not modify files.")
        try:
            if conn["type"] == "cli":
                text, vin, vout = backends.call_cli(name, conn, "read", prompt, cfg.settings, model)
            else:
                corpus, _, _, _ = pack_corpus((paths or []) + (files or []) + ([target] if target else []),
                                             cfg.settings)
                msgs = [{"role": "user", "content": f"<corpus>\n{corpus}\n</corpus>"},
                        {"role": "user", "content": prompt}]
                text, vin, vout = backends.call_api(name, conn, msgs, cfg.settings, model)
            audit(cfg, task=task_name, conn=name, data=conn.get("data"), ok=True, kind="verify",
                  tok_in=vin, tok_out=vout,
                  tok_cached=backends.LAST_USAGE.get("cached", 0) if conn["type"] == "cli" else 0)
            verdict = text.strip()
            if res.kind == "read" and verdict.upper().startswith("CORRECTED"):
                res.text = verdict
                res.conn = f"{res.conn} (corrected by {name})"
            else:
                res.text = f"{res.text}\n[verified by {name}] {verdict}"
            return
        except RelayError as e:
            audit(cfg, task=task_name, conn=name, data=conn.get("data"), ok=False, kind="verify",
                  error=str(e)[:200])
            res.notes.append(f"verifier failed {e}")
    res.notes.append("UNVERIFIED: no verifier available")
