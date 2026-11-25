#!/usr/bin/env python3
"""Stands in for `opencode run --format json` in the desk's tests.

It prints the events the real one prints. What it does is chosen by a word in the message:
FAIL (an error event), SLOW (it never ends by itself), SILENT (no answer), STEPS (three tools,
two seconds apart, before it answers: the phone's live view has time to show them). Otherwise it uses one
tool and answers with the folder it runs in, the session it was given and the last line it was told.
"""

import json
import os
import sys
import time

arguments = sys.argv[1:]
if arguments[-2:] == ["run", "--help"] or arguments == ["run", "--help"]:
    print("FLAGS\n  --standalone    Run with a private server instead of the background service")
    sys.exit(0)
message = arguments[-1]
session = arguments[arguments.index("--session") + 1] if "--session" in arguments else "ses_fake1"


def out(kind: str, **part) -> None:
    print(json.dumps({"type": kind, "sessionID": session, "part": {"messageID": part.pop("message", "msg_1"), **part}}), flush=True)


out("step_start")
if "FAIL" in message:
    print(json.dumps({"type": "error", "sessionID": session, "error": {"type": "unknown", "message": "the model said no"}}), flush=True)
    sys.exit(0)
if "SLOW" in message:
    time.sleep(600)
if "SILENT" in message:
    sys.exit(3)
if "STEPS" in message:
    for command in ("git status", "python -m pytest -q", "git diff --stat"):
        out("tool_use", tool="bash", state={"title": "bash", "input": {"command": command}})
        time.sleep(2)
out("text", text="Let me look.")
out("tool_use", tool="shell", state={"title": "shell", "input": {"command": "ls"}})
out("step_finish")
out("text", message="msg_2", text=f"cwd={os.getcwd()}")
out("text", message="msg_2", text=f"session={session} files={arguments.count('--file')} standalone={'--standalone' in arguments} "
                                  f"last={message.splitlines()[-1]}")
out("step_finish", message="msg_2")
