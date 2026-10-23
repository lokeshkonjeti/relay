"""End-to-end tests: real relay CLI, mock OpenAI-compatible server, fake agent CLIs.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "plugins" / "relay" / "bin" / "relay"
FAKE = ROOT / "tests" / "fakes" / "fake_agent.py"

# model name -> behaviour of the mock API
BEHAVIOUR = {}
REQUESTS = []


class MockAPI(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"models": [{"name": "qwen3:8b"}, {"name": "deepseek-v4-flash:cloud"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"model": req["model"], "auth": self.headers.get("Authorization"),
                         "api-key": self.headers.get("api-key"), "messages": req["messages"]})
        how = BEHAVIOUR.get(req["model"], "ok")
        if how == "429":
            return self._send(429, {"error": "rate limit"})
        last = req["messages"][-1]["content"]
        if how == "truncate":
            text = "```python\nx = 1\n```"
        elif "Write the file" in last:
            text = "```python\ndef test_api():\n    assert 2 + 2 == 4\n```"
        elif last.startswith("Task type") and "Change:" in last or "Change:" in last:
            src = req["messages"][1]["content"]
            body = src.split("\n", 1)[1].rsplit("\n</file>", 1)[0]
            text = "```\n" + body.replace("color: red", "color: blue") + "\n```"
        else:
            text = f"- answer from {req['model']}"
        self._send(200, {"choices": [{"message": {"content": text}}],
                         "usage": {"prompt_tokens": 100, "completion_tokens": 10}})


class RelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockAPI)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        BEHAVIOUR.clear()
        REQUESTS.clear()
        self.tmp = Path(tempfile.mkdtemp(prefix="relay-test-"))
        self.home = self.tmp / "home"
        self.repo = self.tmp / "repo"
        self.bins = self.tmp / "bins"
        self.bins.mkdir()
        self.repo.mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src" / "gcs.py").write_text("PORT = 5600\n" * 3)
        (self.repo / "src" / "app.css").write_text("".join(f".b{i} {{ color: red; }}\n" for i in range(30)))
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        self.log = self.tmp / "agent.log"
        self.env = {
            "PATH": f"{self.bins}:{os.environ['PATH']}",
            "HOME": str(self.tmp),
            "RELAY_HOME": str(self.home),
            "RELAY_POLICY": str(self.tmp / "no-policy.json"),
            "FAKE_LOG": str(self.log),
            "DEEPSEEK_API_KEY": "sk-test",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers

    def tool(self, *names):
        for n in names:
            (self.bins / n).symlink_to(FAKE)

    def relay(self, *args, ok=True, **env):
        e = {**self.env, **{k: str(v) for k, v in env.items()}}
        p = subprocess.run(["python3", str(RELAY), *args], cwd=self.repo, env=e,
                           capture_output=True, text=True, timeout=60)
        if ok and p.returncode != 0:
            self.fail(f"relay {' '.join(args)} failed:\n{p.stdout}\n{p.stderr}")
        return p.stdout + p.stderr, p.returncode

    def base_config(self, mode="public", **conn_over):
        """deepseek + ollama-cloud on the mock server, codex fake CLI."""
        self.tool("codex")
        url = f"http://127.0.0.1:{self.port}/v1"
        self.relay("init", "--mode", mode)
        self.relay("connect", "add", "deepseek", "--base-url", url, "--force")
        self.relay("connect", "add", "ollama-cloud", "--base-url", url)
        return url

    def agent_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    # ---------------------------------------------------------------- tests

    def test_init_detects_tools_and_keys(self):
        self.tool("codex", "gemini")
        out, _ = self.relay("init")
        self.assertIn("codex (data=unverified)", out)
        self.assertIn("gemini (data=unverified)", out)
        self.assertIn("deepseek (data=may-train)", out)
        cfg = json.loads((self.home / "config.json").read_text())
        self.assertEqual(cfg["mode"], "public")
        self.assertIn("fullstack", cfg["packs"])
        self.assertEqual(cfg["roles"]["agent"][0], "codex")

    def test_designated_self_never_calls_a_model(self):
        self.base_config()
        out, _ = self.relay("run", "db-design", "design the orders schema", "--paths", "src")
        self.assertIn("db-design → SELF", out)
        self.assertEqual(REQUESTS, [])
        self.assertEqual(self.agent_calls(), [])

    def test_public_cheap_edit_via_api(self):
        self.base_config()
        out, _ = self.relay("run", "ui-tweak", "make buttons blue", "--files", "src/app.css")
        self.assertIn("ui-tweak → deepseek]", out)
        self.assertIn("edited src/app.css (+30 -30)", out)
        self.assertIn("color: blue", (self.repo / "src/app.css").read_text())
        self.assertEqual(REQUESTS[0]["auth"], "Bearer sk-test")

    def test_public_never_stops_falls_back_on_quota(self):
        self.base_config()
        BEHAVIOUR["deepseek-chat"] = "429"
        out, _ = self.relay("run", "er-diagram", "ER diagram of models", "--paths", "src",
                            "--target", "docs/er.md")
        self.assertIn("er-diagram → ollama-cloud]", out)
        self.assertIn("failed deepseek: HTTP 429", out)
        self.assertTrue((self.repo / "docs/er.md").exists())

    def test_public_everything_down_means_self(self):
        self.base_config()
        BEHAVIOUR["deepseek-chat"] = BEHAVIOUR["deepseek-v4-flash:cloud"] = "429"
        out, _ = self.relay("run", "read-codebase", "which port?", "--paths", "src", FAKE_FAIL="codex")
        self.assertIn("SELF]", out)
        self.assertIn("no delegate available", out)
        self.assertEqual(out.count("failed"), 3)

    def test_private_mode_fails_closed(self):
        self.base_config(mode="private")
        out, _ = self.relay("run", "unit-tests", "tests for gcs", "--paths", "src/gcs.py",
                            "--target", "tests/test_gcs.py")
        self.assertIn("SELF]", out)
        self.assertEqual(REQUESTS, [], "private mode must not touch may-train/unverified endpoints")
        self.assertEqual(self.agent_calls(), [])
        self.relay("connect", "set", "codex", "data=no-train")
        out, _ = self.relay("run", "unit-tests", "tests for gcs", "--paths", "src/gcs.py",
                            "--target", "tests/test_gcs.py")
        self.assertIn("unit-tests → codex]", out)
        self.assertEqual(REQUESTS, [])
        self.assertIn("workspace-write", self.agent_calls()[-1]["args"])

    def test_private_allows_local_models(self):
        url = self.base_config(mode="private")
        self.relay("connect", "add", "ollama-local", "--base-url", url, "--model", "qwen3:8b")
        out, _ = self.relay("run", "read-codebase", "which port?", "--paths", "src")
        self.assertIn("→ ollama-local]", out)
        self.assertEqual([r["model"] for r in REQUESTS], ["qwen3:8b"])

    def test_multi_account_env_and_preset_roles(self):
        self.tool("codex")
        self.relay("init")
        self.relay("connect", "add", "codex", "--name", "codex-work", "--env",
                   "CODEX_HOME=~/.codex-work", "--data", "no-train")
        self.relay("connect", "rm", "codex")
        self.relay("mode", "private")
        out, _ = self.relay("run", "small-fix", "add a null check", "--files", "src/gcs.py")
        self.assertIn("small-fix → codex-work]", out)  # role 'codex' matched the preset
        call = self.agent_calls()[-1]
        self.assertEqual(call["CODEX_HOME"], str(self.tmp / ".codex-work"))
        self.assertEqual(call["RELAY_DEPTH"], "1")
        self.assertIn("edited by codex", (self.repo / "src/gcs.py").read_text())

    def test_second_claude_account(self):
        self.tool("claude")
        self.relay("init")
        self.relay("connect", "add", "claude", "--name", "claude-work", "--env",
                   "CLAUDE_CONFIG_DIR=~/.claude-work", "--data", "no-train")
        self.relay("task", "set", "docs", "--route", "claude-work")
        out, _ = self.relay("run", "docs", "README for src", "--paths", "src", "--target", "README.md")
        self.assertIn("docs → claude-work]", out)
        call = self.agent_calls()[-1]
        self.assertEqual(call["CLAUDE_CONFIG_DIR"], str(self.tmp / ".claude-work"))
        self.assertIn("acceptEdits", call["args"])

    def test_custom_task_and_model_override(self):
        self.base_config()
        self.relay("task", "set", "sql-explain", "--kind", "read", "--desc", "explain slow queries",
                   "--route", "ollama-cloud:qwen3-coder:480b-cloud,deepseek")
        out, _ = self.relay("run", "sql-explain", "why slow?", "--paths", "src")
        self.assertIn("sql-explain → ollama-cloud:qwen3-coder:480b-cloud]", out)
        self.assertEqual(REQUESTS[-1]["model"], "qwen3-coder:480b-cloud")

    def test_verify_corrects_read(self):
        self.base_config()
        out, _ = self.relay("run", "read-codebase", "which port?", "--paths", "src", "--verify",
                            FAKE_VERDICT="CORRECTED\n- port is 8554")
        self.assertIn("(corrected by codex)", out)
        self.assertIn("8554", out)
        self.assertIn("read-only", self.agent_calls()[-1]["args"])

    def test_api_edit_truncation_guard(self):
        self.base_config()
        BEHAVIOUR["deepseek-chat"] = "truncate"
        out, _ = self.relay("run", "ui-tweak", "blue buttons", "--files", "src/app.css")
        self.assertIn("looks truncated", out)
        self.assertIn("→ ollama-cloud]", out)  # next in the @cheap chain, after the bad edit was rejected
        css = (self.repo / "src/app.css").read_text()
        self.assertIn("color: blue", css)
        self.assertEqual(len(css.splitlines()), 30, "the truncated version must never be written")

    def test_write_refuses_overwrite(self):
        self.base_config()
        out, code = self.relay("run", "docs", "x", "--target", "src/gcs.py", ok=False)
        self.assertEqual(code, 2)
        self.assertIn("exists", out)

    def test_nested_call_blocked(self):
        self.base_config()
        out, _ = self.relay("run", "read-codebase", "q", "--paths", "src", RELAY_DEPTH=1)
        self.assertIn("nested delegation blocked", out)
        self.assertEqual(REQUESTS, [])

    def test_project_file_cannot_add_connections(self):
        self.base_config()
        (self.repo / ".relay.json").write_text(json.dumps(
            {"connections": {"evil": {"type": "cli", "bin": "rm"}}}))
        out, code = self.relay("tasks", ok=False)
        self.assertEqual(code, 2)
        self.assertIn("not allowed in a project file", out)

    def test_project_private_overrides_global_public(self):
        self.base_config()
        self.relay("mode", "private", "--project")
        out, _ = self.relay("run", "docs", "readme", "--paths", "src", "--target", "README.md")
        self.assertIn("SELF]", out)
        self.assertEqual(REQUESTS, [])

    def test_enterprise_policy(self):
        url = self.base_config()
        policy = self.tmp / "policy.json"
        policy.write_text(json.dumps({
            "force_mode": "private", "lock": True, "allow_connections": ["corp-azure"],
            "connections": {"corp-azure": {"type": "api", "preset": "azure-openai", "base_url": url,
                                           "auth": "api-key", "key_env": "AZURE_KEY", "model": "gpt-corp",
                                           "data": "no-train", "cost": "paid"}}}))
        env = {"RELAY_POLICY": str(policy), "AZURE_KEY": "az-test"}
        out, code = self.relay("mode", "public", ok=False, **env)
        self.assertIn("forced to private", out)
        out, code = self.relay("connect", "add", "openrouter", ok=False, **env)
        self.assertIn("locked by enterprise policy", out)
        out, _ = self.relay("run", "docs", "readme", "--paths", "src", "--target", "README.md", **env)
        self.assertIn("docs → corp-azure]", out)
        self.assertEqual(REQUESTS[-1]["api-key"], "az-test")
        self.assertIsNone(REQUESTS[-1]["auth"])

    def test_via_cannot_escape_private(self):
        self.base_config(mode="private")
        out, _ = self.relay("run", "read-codebase", "q", "--paths", "src", "--via", "deepseek")
        self.assertIn("SELF]", out)
        self.assertIn("not allowed in private mode", out)
        self.assertEqual(REQUESTS, [])

    def test_brief_listing_and_audit(self):
        self.base_config()
        out, _ = self.relay("tasks", "--brief")
        self.assertIn("Claude's by design (don't delegate): architecture", out)
        self.assertIn("er-diagram [write]", out)
        self.assertLess(len(out), 2500, "brief listing must stay small; it is loaded into Claude's context")
        self.relay("run", "read-codebase", "which port?", "--paths", "src")
        rows = [json.loads(l) for l in (self.home / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["conn"], "ollama-cloud")
        self.assertEqual(rows[-1]["data"], "unverified")
        self.assertTrue(rows[-1]["files"][0].endswith("app.css"))
        out, _ = self.relay("stats")
        self.assertIn("tokens of reading/writing kept out", out)
        out, _ = self.relay("audit")
        self.assertIn("read-codebase", out)

    def test_secrets_never_packed(self):
        self.base_config()
        (self.repo / "src" / ".env").write_text("DB_PASSWORD=hunter2\n")
        (self.repo / "src" / "server.pem").write_text("-----BEGIN KEY-----\n")
        self.relay("run", "read-codebase", "what config exists?", "--paths", "src")
        sent = json.dumps(REQUESTS[-1]["messages"])
        self.assertNotIn("hunter2", sent)
        self.assertNotIn("BEGIN KEY", sent)
        self.assertIn("gcs.py", sent)

    def test_antigravity_preset_flags_and_timeout(self):
        self.tool("agy")
        out, _ = self.relay("init")
        self.assertIn("antigravity (data=unverified)", out)
        cfg = json.loads((self.home / "config.json").read_text())
        self.assertEqual(cfg["connections"]["antigravity"]["bin"], str(self.bins / "agy"))
        out, _ = self.relay("run", "review", "is this safe?", "--paths", "src", "--via", "antigravity")
        self.assertIn("review → antigravity]", out)
        args = self.agent_calls()[-1]["args"]
        self.assertEqual(args[0], "-p")
        self.assertIn("--print-timeout", args)
        self.assertEqual(args[args.index("--mode") + 1], "plan")
        self.relay("run", "ui-first-pass", "landing page", "--target", "web/page.tsx", "--via", "antigravity")
        args = self.agent_calls()[-1]["args"]
        self.assertEqual(args[args.index("--mode") + 1], "accept-edits")
        # per-connection timeout wins over the global one, then falls back to Claude
        self.relay("connect", "set", "antigravity", "timeout_sec=1")
        out, _ = self.relay("run", "review", "q", "--paths", "src", "--via", "antigravity", FAKE_SLEEP=3)
        self.assertIn("timed out after 1s", out)
        self.assertIn("SELF]", out)

    def test_finds_cli_outside_path_and_fresh_init(self):
        hidden = self.tmp / ".local" / "bin"
        hidden.mkdir(parents=True)
        (hidden / "codex").symlink_to(FAKE)
        out, _ = self.relay("init", OLLAMA_API_KEY="ok-test")
        self.assertIn("codex (data=unverified)", out)
        self.assertIn("ollama-api (data=unverified)", out)
        self.assertIn("not found: gemini, agy", out)
        cfg = json.loads((self.home / "config.json").read_text())
        self.assertEqual(cfg["connections"]["codex"]["bin"], str(hidden / "codex"))
        self.assertEqual(cfg["connections"]["ollama-api"]["base_url"], "https://ollama.com/v1")
        self.relay("connect", "set", "codex", "data=no-train")
        out, _ = self.relay("init", "--fresh")
        self.assertIn("started fresh", out)
        self.assertEqual(len(list(self.home.glob("config.backup-*.json"))), 1)
        cfg = json.loads((self.home / "config.json").read_text())
        self.assertEqual(cfg["connections"]["codex"]["data"], "unverified")

    def test_missing_cli_hint(self):
        self.base_config()
        self.relay("connect", "add", "gemini")
        out, _ = self.relay("doctor")
        self.assertIn("restart Claude Code", out)

    def test_doctor_runs(self):
        self.base_config()
        out, _ = self.relay("doctor", "--probe")
        self.assertIn("probe ok", out)
        self.assertIn("db-design", out)


if __name__ == "__main__":
    unittest.main()
