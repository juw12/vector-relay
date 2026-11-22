#!/usr/bin/env python3
"""Stands in for the `cairn` command in the desk's tests.

`vibe hand phone` prints the line FAKE_CAIRN_PHONE holds; `handoff ask` prints what the other computer
would answer: the computer, the app and the last line of the text it was sent. With SLOW it fails as Cairn does
when the other computer did not answer in time.
"""

import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
if arguments[:3] == ["vibe", "hand", "phone"]:
    print(os.environ.get("FAKE_CAIRN_PHONE", "PHONE to=here agent=opencode"))
    sys.exit(0)
if arguments[:2] == ["handoff", "ask"]:
    to = arguments[arguments.index("--to") + 1]
    app = arguments[arguments.index("--agent") + 1]
    text = Path(arguments[arguments.index("--file") + 1]).read_text(encoding="utf-8")
    if "SLOW" in text:
        print(f"{to} has not answered in 1 minutes. The work is still left for it.")
        sys.exit(3)
    print(f"from={to} app={app} title={arguments[2]} last={text.splitlines()[-1]}")
    sys.exit(0)
sys.exit(2)
