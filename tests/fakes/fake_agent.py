#!/usr/bin/env python3
"""Stands in for codex / gemini / claude in tests.

Logs its argv and account env vars, then acts on the prompt:
  "Create the file X."      -> writes X
  "Relevant files: a b"      -> appends a line to each file
  "--- ANSWER ---" / "Review the change" -> verdict from FAKE_VERDICT (default VERIFIED/OK)
  otherwise                  -> a short answer
FAKE_FAIL=<toolname> makes that tool exit 1 with a quota message.
"""
import json
import os
import re
import sys
from pathlib import Path

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps({"tool": tool, "args": args,
                             "CODEX_HOME": os.environ.get("CODEX_HOME"),
                             "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR"),
                             "RELAY_DEPTH": os.environ.get("RELAY_DEPTH")}) + "\n")

if os.environ.get("FAKE_SLEEP"):
    import time
    time.sleep(float(os.environ["FAKE_SLEEP"]))

if tool in os.environ.get("FAKE_FAIL", "").split(","):
    print("error: usage limit reached (429)", file=sys.stderr)
    sys.exit(1)

out_file = None
if "--output-last-message" in args:
    out_file = args[args.index("--output-last-message") + 1]
prompt = args[-1] if tool == "codex" else args[args.index("-p") + 1]

if "--- ANSWER ---" in prompt:
    reply = os.environ.get("FAKE_VERDICT", "VERIFIED\n- matches the files")
elif "Review the change" in prompt:
    reply = os.environ.get("FAKE_VERDICT", "OK looks correct")
elif m := re.search(r"Create the file (\S+?)\.\n", prompt):
    target = Path(m.group(1))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"# written by {tool}\ndef test_generated():\n    assert True\n")
    reply = f"Created {target} with one test."
elif m := re.search(r"Relevant files: (.+)\n", prompt):
    for f in m.group(1).split():
        with open(f, "a") as fh:
            fh.write(f"# edited by {tool}\n")
    reply = f"Edited {m.group(1)}"
else:
    reply = f"{tool} answer: the port is 5600"

if out_file:
    Path(out_file).write_text(reply)
else:
    print(reply)
