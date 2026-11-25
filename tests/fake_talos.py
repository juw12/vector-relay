#!/usr/bin/env python3
"""Stands in for `talos run --yes --events` in the desk's tests.

It prints the lines the real one prints: {"event": ..., "data": ...}, the last one "result".
A word in the message chooses what it does: STOPPED (the run ends on a model error), UNCHECKED
(nothing was changed, so no proof line). Otherwise it uses one tool and answers with the session it was
given, the model it was told to use and the last line of the message.
"""

import json
import os
import sys

arguments = sys.argv[1:]
message = arguments[-1]
session = arguments[arguments.index("--session") + 1] if "--session" in arguments else "tal_fake1"
model = arguments[arguments.index("-m") + 1] if arguments[:1] == ["-m"] else "pegaai/Pega-Coding"


def out(event: str, data) -> None:
    print(json.dumps({"event": event, "data": data}), flush=True)


if arguments[-4:-1] != ["run", "--yes", "--events"] and "--session" not in arguments:
    out("result", {"text": f"odd arguments: {arguments[:-1]}", "session": session, "stopped": "model error"})
    sys.exit(1)
out("session", {"session": session, "model": model})
if "STOPPED" in message:
    out("result", {"text": "No model answered: the office gateway is down.", "session": session, "stopped": "model error",
                   "proof": {"level": "nothing changed"}, "usage": {}, "steps": 0})
    sys.exit(1)
out("assistant", "Let me look.")
out("tool", {"name": "bash", "arguments": {"command": "python area.py"}})
out("tool_result", {"name": "bash", "result": "1200"})
out("warning", "The free services are busy. Waiting 46 s.")
answer = f"session={session} model={model} env={os.environ.get('TALOS_TEST', '')} last={message.splitlines()[-1]}"
out("assistant", answer)
out("result", {"text": answer, "session": session, "stopped": "",
               "proof": {"level": "nothing changed" if "UNCHECKED" in message else "checked"}, "usage": {}, "steps": 2})
