"""Vector Relay: see and steer the AI chats on this PC from your phone.

    phone  --prompt-->  relay  --prompt-->  this PC  -->  the chat that is in phone mode
    phone  <--feed----  relay  <--feed----  this PC  <--  updates, results and pictures the chat posts

Vector Relay never touches the chat app itself. The chat's own agent runs three commands with the
tools it already has:

    vector_relay.py on --label "Atlas FA"   phone mode on for this chat; prints its chat id
    vector_relay.py say "..."               post an update, a result, an error or a picture
    vector_relay.py listen                  wait for the next prompt from the phone and print it

Several chats can be in phone mode at once: each has its own id, and the phone picks which one
a prompt goes to.

A chat that is not in phone mode can still say what it is doing:

    vector_relay.py step --title "Atlas FA" "reading the leak data"   a row on the board; prints its chat id
    vector_relay.py step --chat <id> --end shipped "report sent"      the chat is over
    vector_relay.py board                                             every chat on this PC and its last step
    vector_relay.py view                                              the page, on this PC's own browser

The page shows the board of every computer it knows, running chats first: "All chats".

Everything that leaves this PC is sealed with AES-256-GCM. The key is made here and reaches the
phone only inside the QR code, after the "#" of the address, which a browser sends to no server.
The relay (functions/ in this repo, on Cloudflare Pages with a D1 database) holds ciphertext it
cannot read, and can at worst lose or delay it. The phone needs the internet, not your Wi-Fi.

`pair --code` links a phone with 4 digits instead of the QR code. The digits never travel: PC and
phone each prove they know them (SPEKE), and the link with the key then comes sealed with a key the
two made in that exchange. The relay learns neither, and a guess costs one of three tries.

A prompt is carried out once: its id is remembered, and one older than PROMPT_MAX_AGE_S is refused,
so a recorded prompt cannot be played again and one the phone marked "not sent" does not arrive later.

Data lives in %LOCALAPPDATA%\\VectorRelay (or the folder VECTOR_RELAY_HOME names).

Local mode (`pair --local`) needs no relay and no internet: this PC serves the phone page itself and
answers the phone directly, for a phone on the same Wi-Fi. Everything is sealed the same way.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.3.1"
RELAY = "https://vector-relay.pages.dev"  # the page and the relay live at one address
KINDS = ("update", "result", "error", "note", "prompt")
PROMPT_ID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
CHAT_ID = re.compile(r"^[a-z0-9]{4}$")
# http is for this PC and its own network only: a name ending in .local, or a private address
NEAR = r"(127\.0\.0\.1|localhost|[A-Za-z0-9-]+\.local|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2})"
RELAY_ADDRESS = re.compile(r"^(https://[^/\s]+|http://" + NEAR + r"(:\d+)?)$")
LOCAL_PORT = 47990  # local mode: where this PC serves the phone page
DOCS = Path(__file__).resolve().parent / "docs"
PAGE_FILES = {
    "": "index.html", "index.html": "index.html", "app.js": "app.js", "softcrypto.js": "softcrypto.js",
    "styles.css": "styles.css", "icon.svg": "icon.svg", "icon-180.png": "icon-180.png",
    "versions.json": "versions.json",
}
PAGE_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json; charset=utf-8",
}
MAX_ASK_BYTES = 1_200_000  # one sealed question from the phone, as the relay allows
ANSWERS_KEPT, ANSWER_KEPT_S = 60, 300

MAX_PROMPT = 20_000  # characters in one prompt from the phone
PROMPT_MAX_AGE_S = 15  # shorter than the phone waits (20 s): a prompt the phone gave up on is never carried out later
PHOTO_MAX_AGE_S = 45  # ... and with a photo the phone waits 50 s: a photo takes a while to send
SEEN_KEPT = 300
KEEP_EVENTS = 500  # the feed file is cut back to this many
FEED_EVENTS, FEED_TEXT, FEED_BYTES = 60, 6_000, 600_000  # one live answer to the phone
STATE_EVENTS, STATE_TEXT, STATE_BYTES = 30, 1_500, 150_000  # the copy the relay keeps for a PC that is off
FULL_TEXT_BYTES = 1_000_000
PICTURE_SIDE, PICTURE_BYTES = 1600, 600_000  # a picture for the phone: longest side, and size as a JPEG
PHOTO_CHARS = 1_000_000  # a photo from the phone, as base64
FILES_KEPT_S = 14 * 24 * 3600
CHAT_KEPT_S = 24 * 3600  # a chat that went off stays on the phone this long

ENDINGS = ("shipped", "partial", "dropped", "done")  # how a chat on the board can end
BOARD_ROWS = 40  # chats on the board: the running ones first, then the ones that ended last
BOARD_STEPS, BOARD_STEPS_KEPT = 5, 20  # the last steps of a chat that travel, and that this PC keeps
BOARD_TITLE, BOARD_STEP = 140, 240  # characters
BOARD_BYTES = 40_000  # the whole board, inside the copy the relay keeps
BOARD_KEPT_S = 7 * 24 * 3600  # a chat that ended stays on the board this long
BOARD_QUIET_S = 6 * 3600  # a running chat with no step for this long has probably stopped
NOTES_EVERY_S = 15  # how often the folder of session notes is looked at
RELAY_STATE_CHARS = 290_000  # the relay takes a sealed copy of 300,000 characters

PUBLISH_GAP_S = 120  # the relay's free plan allows 1,000 stored writes a day
HEARTBEAT_S = 30 * 60
ASK_BUSY_S, ASK_BUSY_FOR_S = 0.7, 120  # how often the helper looks for a question while the phone is in use
ASK_RECENT_S, ASK_RECENT_FOR_S = 2.0, 30 * 60
ASK_IDLE_S = 5.0
TAKE_HOLD_S = 20  # the relay holds the helper's request this long, and answers the moment a question comes
ASK_RETRY_S = 15
LISTEN_BEAT_S, LISTEN_FRESH_S = 5, 15
IDLE_ON_S = 6 * 3600  # the helper stops this long after the chat last said or heard anything
IDLE_OFF_S = 10 * 60  # ... and this long after phone mode was switched off
TIMEOUT_S = 35  # longer than the relay holds a request
CODE_VALID_S = 5 * 60  # a 4-digit code links a phone this long
CODE_TRIES = 3  # ... and takes this many answers; then it is dead, right or wrong
CODE_GRACE_S = 15  # the phone gets this long to fetch the last answer before the room is wiped
ROOM_EVERY_S = 5 * 60  # the room where a code is used has a new name this often
KEEP_IDLE_S = 30  # a connection that was silent this long is not used again: proxies drop them


# ---------------------------------------------------------------- small things
def home() -> Path:
    named = os.environ.get("VECTOR_RELAY_HOME")
    folder = Path(named) if named else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "VectorRelay"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def seal(key: bytes, data: dict) -> str:
    """JSON -> base64url(nonce + ciphertext + tag), the layout WebCrypto's AES-GCM uses too."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    plain = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b64(nonce + AESGCM(key).encrypt(nonce, plain, None))


def unseal(key: bytes, text: str) -> dict | None:
    """None for anything that was not sealed with this key."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = unb64(text.strip())
        data = json.loads(AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8"))
    except (InvalidTag, ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    """Whole file or nothing: another process may be reading it right now."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:  # Windows: somebody has the old one open this instant
            time.sleep(0.02)
    os.replace(temporary, path)


@contextmanager
def locked(folder: Path):
    """One writer at a time for the feed; the helper and the chat's commands are separate processes."""
    path = folder / "lock"
    deadline = time.time() + 10
    while True:
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            try:
                if time.time() - path.stat().st_mtime > 15:  # left behind by a process that died
                    path.unlink()
            except OSError:
                pass
            if time.time() > deadline:
                raise TimeoutError("the feed is locked by another process")
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(handle)
        for attempt in range(20):
            try:
                path.unlink()
                break
            except FileNotFoundError:
                break
            except PermissionError:
                time.sleep(0.02)


# ---------------------------------------------------------------- settings and mode
def settings(folder: Path) -> dict:
    return read_json(folder / "settings.json", {})


def paired(found: dict) -> bool:
    return all(found.get(name) for name in ("relay", "channel", "key", "write"))


def ticket(found: dict) -> str:
    """What the phone needs, packed for the part of the address after the #."""
    return b64(json.dumps({"u": found["relay"], "c": found["channel"], "k": found["key"]}).encode("utf-8"))


def chats(folder: Path) -> dict[str, dict]:
    """Every chat that is or was in phone mode: id -> label, on, since."""
    found = read_json(folder / "chats.json", {})
    if not isinstance(found, dict):
        return {}
    return {
        chat: {"label": str(entry.get("label", "")), "on": entry.get("on") is True, "since": int(entry.get("since", 0))}
        for chat, entry in found.items() if isinstance(entry, dict) and CHAT_ID.match(str(chat))
    }


def chats_on(folder: Path) -> list[str]:
    return [chat for chat, entry in chats(folder).items() if entry["on"]]


def mode(folder: Path) -> dict:
    """All chats seen as one: on while any is, with the label when it is the only one."""
    found = [entry for entry in chats(folder).values() if entry["on"]]
    return {"on": bool(found), "label": found[0]["label"] if len(found) == 1 else "",
            "since": max((entry["since"] for entry in found), default=0)}


def pick_chat(folder: Path, named: str = "") -> tuple[str, str]:
    """(chat id, "") or ("", why not): the named chat, or the only one that is in phone mode."""
    found = chats(folder)
    if named:
        return (named, "") if named in found else ("", "unknown")
    on = [chat for chat, entry in found.items() if entry["on"]]
    if len(on) == 1:
        return on[0], ""
    return "", "several" if on else "off"


def touch(folder: Path) -> None:
    (folder / "active").write_text(str(int(time.time())), encoding="ascii")


def last_active(folder: Path) -> float:
    try:
        return (folder / "active").stat().st_mtime
    except OSError:
        return 0.0


def listening(folder: Path, chat: str = "") -> bool:
    """True while a chat sits in `listen`: the named one, or any."""
    for beat in ([folder / f"listening-{chat}"] if chat else folder.glob("listening-*")):
        try:
            if time.time() - beat.stat().st_mtime < LISTEN_FRESH_S:
                return True
        except OSError:
            pass
    return False


def chat_list(folder: Path) -> list[dict]:
    """What the phone shows as chats: those in phone mode, and those that left it within a day."""
    now = time.time()
    return [
        {"id": chat, "label": entry["label"], "on": entry["on"], "since": entry["since"], "listening": listening(folder, chat)}
        for chat, entry in chats(folder).items() if entry["on"] or now - entry["since"] < CHAT_KEPT_S
    ]


def files(folder: Path) -> Path:
    path = folder / "files"
    path.mkdir(exist_ok=True)
    return path


def make_picture(source: Path, folder: Path) -> dict:
    """A copy of the picture small enough for the phone, as a JPEG in files/."""
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        picture = ImageOps.exif_transpose(opened)
        if picture.mode in ("RGBA", "LA", "P"):
            picture = picture.convert("RGBA")
            paper = Image.new("RGB", picture.size, "white")
            paper.paste(picture, mask=picture.split()[-1])
            picture = paper
        else:
            picture = picture.convert("RGB")
    import io

    side, quality = PICTURE_SIDE, 85
    while True:
        small = picture.copy()
        small.thumbnail((side, side))
        packed = io.BytesIO()
        small.save(packed, "JPEG", quality=quality, optimize=True)
        if packed.tell() <= PICTURE_BYTES or side <= 500:
            break
        quality, side = (quality - 15, side) if quality > 60 else (70, int(side * 0.8))
    name = secrets.token_hex(8) + ".jpg"
    (files(folder) / name).write_bytes(packed.getvalue())
    return {"f": name, "w": small.width, "h": small.height}


# ---------------------------------------------------------------- the feed (PC -> phone)
def read_feed(folder: Path) -> list[dict]:
    events = []
    try:
        lines = (folder / "feed.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("n"), int):
            events.append(event)
    return events


def add_event(folder: Path, kind: str, text: str, source: str = "pc", **more) -> dict:
    with locked(folder):
        events = read_feed(folder)
        number = events[-1]["n"] + 1 if events else 1
        event = {"n": number, "t": int(time.time()), "kind": kind, "from": source, "text": text, **more}
        line = json.dumps(event, ensure_ascii=False) + "\n"
        path = folder / "feed.jsonl"
        if len(events) >= KEEP_EVENTS + 100:
            kept = [json.dumps(old, ensure_ascii=False) + "\n" for old in events[-KEEP_EVENTS:]]
            path.write_text("".join(kept) + line, encoding="utf-8")
        else:
            with open(path, "a", encoding="utf-8") as feed:
                feed.write(line)
    return event


_named: list[str] = []  # this computer's name, asked for once


def device_name() -> str:
    """What this computer is called on the page until it is given a name there."""
    if os.environ.get("VECTOR_RELAY_DEVICE_NAME"):
        return os.environ["VECTOR_RELAY_DEVICE_NAME"][:40]
    if not _named:
        name = socket.gethostname().split(".")[0]
        if sys.platform == "darwin":  # a Mac's network name can be its address; the name in System Settings is not
            try:
                asked = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5)
                name = asked.stdout.strip() or name
            except (OSError, subprocess.SubprocessError):
                pass
        _named.append("This computer" if name.isdigit() or not name else name[:40])
    return _named[0]


def cut_text(text: str, most: int) -> tuple[str, bool]:
    return (text, False) if len(text) <= most else (text[:most].rstrip(), True)


def feed_answer(folder: Path, after: int = 0, limit: int = FEED_EVENTS, text_max: int = FEED_TEXT,
                bytes_max: int = FEED_BYTES) -> dict:
    """The newest events after number `after`, long texts cut, small enough for the relay."""
    events = read_feed(folder)
    last = events[-1]["n"] if events else 0
    if after > last:  # the phone remembers a feed that was cleared since
        after = 0
    chosen, size = [], 0
    for event in reversed([event for event in events if event["n"] > after][-limit:]):
        text, cut = cut_text(str(event.get("text", "")), text_max)
        size += len(text.encode("utf-8"))
        if size > bytes_max and chosen:
            break
        chosen.append({**event, "text": text, **({"cut": True} if cut else {})})
    chosen.reverse()
    return {
        "ok": True, "v": VERSION, "pc": int(time.time()), "mode": mode(folder), "listening": listening(folder),
        "deviceName": device_name(),
        "chats": chat_list(folder), "waiting": waiting_ids(folder), "last": last, "events": chosen,
        "board": board_rows(folder),
    }


# ---------------------------------------------------------------- the board (what every chat is doing)
# One row per chat: what it is about, whether it still runs, and its last few steps. Rows come from
# two places. A chat posts its own steps with `step`; a chat in phone mode gets a row by itself, and
# its updates are its steps. And a folder of session notes, when `board --notes` names one: Markdown
# files with a head (title, status, started, machine, agent) and a "## Log" of "- HH:MM text" lines,
# the way Cairn writes them. The board travels sealed like the feed, inside the copy the relay
# keeps, so the page shows the chats of every computer, also of one that is asleep.
NOTE_LOG = re.compile(r"^- (\d{1,2}):(\d{2})(?::\d{2})? (?:([a-z]{2,12}): )?(.*)$")
NOTE_PROGRESS = re.compile(r"^\[(\d{1,3})%\]\s*")
NOTE_DAY = re.compile(r"^(\d{4}-\d{2}-\d{2})")  # a note named after its day is not opened once that day is too long ago
_notes: dict = {"folder": "", "at": 0.0, "files": {}, "rows": []}  # the notes folder as it was last read


def one_line(text: str, most: int) -> str:
    words, cut = cut_text(" ".join(str(text).split()), most)
    return words[:most - 1].rstrip() + "…" if cut else words


def board_own(folder: Path) -> dict[str, dict]:
    """The rows the chats of this PC made themselves: chat id -> title, agent, state, progress, times, steps."""
    found = read_json(folder / "board.json", {})
    rows = {}
    for chat, row in (found.items() if isinstance(found, dict) else []):
        if not isinstance(row, dict) or not CHAT_ID.match(str(chat)):
            continue
        try:
            rows[chat] = {
                "title": str(row.get("title", "")), "agent": str(row.get("agent", "")),
                "state": row["state"] if row.get("state") in ("live", *ENDINGS) else "live",
                "progress": None if row.get("progress") is None else int(row["progress"]),
                "started": int(row["started"]), "updated": int(row["updated"]), "own": row.get("own") is True,
                "steps": [[int(step[0]), str(step[1])] for step in row.get("steps", [])],
            }
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return rows


def board_step(folder: Path, chat: str, text: str = "", title: str = "", agent: str = "",
               progress: int | None = None, end: str = "", own: bool = True) -> dict:
    """One step of one chat on the board. Makes the chat's row when it has none."""
    now = int(time.time())
    with locked(folder):
        rows = {name: row for name, row in board_own(folder).items()
                if row["state"] == "live" or now - row["updated"] < BOARD_KEPT_S}
        row = rows.get(chat) or {"title": "", "agent": "", "state": "live", "progress": None,
                                 "started": now, "updated": now, "own": False, "steps": []}
        row["state"] = end or "live"  # a chat that says something after it ended runs again
        if title:
            row["title"] = one_line(title, BOARD_TITLE)
        if agent:
            row["agent"] = one_line(agent, 20)
        if progress is not None:
            row["progress"] = max(0, min(100, progress))
        if one_line(text, BOARD_STEP):
            row["steps"] = [*row["steps"], [now, one_line(text, BOARD_STEP)]][-BOARD_STEPS_KEPT:]
        row["own"] = row["own"] or own
        row["updated"] = now
        rows[chat] = row
        write_json(folder / "board.json", rows)
    return row


def note_time(text: str) -> int:
    """"2026-11-21T10:01" on this PC's clock, in seconds. 0 for anything else."""
    try:
        return int(time.mktime(time.strptime(text.strip()[:16].replace(" ", "T"), "%Y-%m-%dT%H:%M")))
    except (ValueError, OverflowError):
        return 0


def read_note(path: Path) -> dict | None:
    """One session note as a row of the board. None for a file that is not one."""
    try:
        with open(path, encoding="utf-8", errors="replace") as note:
            text = note.read(200_000)
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    head, _, body = text[3:].partition("\n---")
    fields: dict[str, str] = {}
    for line in head.splitlines():
        name, colon, value = line.partition(":")
        if colon and name.strip().isidentifier():
            value = value.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].replace('\\"', '"')
            fields[name.strip().lower()] = value
    started, status = note_time(fields.get("started", "")), fields.get("status", "").lower()
    if not started or not status:
        return None
    state = "live" if status == "live" else status if status in ENDINGS else "done"
    day = time.localtime(started)
    midnight = int(time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1)))
    steps, progress, last, in_log, goal = [], None, started - started % 60, False, ""
    for line in body.splitlines():
        if line.startswith("## "):
            in_log = line.strip().lower() == "## log"
            continue
        if not in_log:
            goal = goal or line.strip()
            continue
        found = NOTE_LOG.match(line)
        if not found:
            continue
        moment = midnight + int(found[1]) * 3600 + int(found[2]) * 60
        if moment < last - 12 * 3600:  # the log went on past midnight; a small step back is a line out of order
            midnight += 86400
            moment += 86400
        last = max(last, moment)
        kind, words = found[3] or "", found[4].strip()
        marked = NOTE_PROGRESS.match(words)
        if marked:
            progress, words = min(100, int(marked[1])), words[marked.end():]
        if kind == "start":  # the title says the same
            continue
        if kind not in ("", "note", "end"):
            words = f"{kind.capitalize()}: {words}"
        steps.append([moment, one_line(words, BOARD_STEP)])
    updated = max(started, last, note_time(fields.get("turn_at", "")))
    if state != "live" and note_time(fields.get("ended", "")) >= started:  # the note says itself when it ended
        updated = note_time(fields["ended"])
    row = {
        "id": path.stem[:120], "src": "note", "title": one_line(fields.get("title") or goal or path.stem, BOARD_TITLE),
        "agent": one_line(fields.get("agent", ""), 20), "machine": one_line(fields.get("machine", ""), 40),
        "state": state, "progress": progress, "started": started, "updated": updated, "steps": steps[-BOARD_STEPS:],
    }
    if state == "live" and fields.get("turn") in ("working", "waiting"):
        row["turn"] = fields["turn"]
    return row


def note_rows(notes: Path, machine: str = "", agent: str = "") -> list[dict]:
    """The session notes in a folder as rows. A file is read again only when it changed."""
    now = time.time()
    if _notes["folder"] != str(notes):
        _notes.update(folder=str(notes), at=0.0, files={}, rows=[])
    if now - _notes["at"] >= NOTES_EVERY_S:
        files = {}
        oldest = time.strftime("%Y-%m-%d", time.localtime(now - BOARD_KEPT_S - 2 * 86400))
        try:
            entries = list(os.scandir(notes))
        except OSError:
            entries = []
        for entry in entries:
            named = NOTE_DAY.match(entry.name)
            if not entry.name.endswith(".md") or (named is not None and named[1] < oldest):
                continue
            try:
                found = entry.stat()
            except OSError:
                continue
            mark = (found.st_mtime_ns, found.st_size)
            old = _notes["files"].get(entry.name)
            files[entry.name] = old if old is not None and old[0] == mark else (mark, read_note(Path(entry.path)))
        _notes.update(at=now, files=files, rows=[row for mark, row in files.values() if row is not None])
    return [row for row in _notes["rows"]
            if (not machine or row["machine"].lower() == machine.lower()) and (not agent or row["agent"].lower() == agent.lower())]


def board_rows(folder: Path, most: int = BOARD_ROWS, bytes_max: int = BOARD_BYTES) -> list[dict]:
    """The board as it travels: the running chats first, then the ones that ended last, small enough for the relay."""
    now, found, phone = time.time(), settings(folder), chats(folder)
    rows = [
        {"id": chat, "chat": chat, "title": row["title"] or phone.get(chat, {}).get("label", "") or f"Chat {chat}",
         "agent": row["agent"], "machine": "", "state": row["state"], "progress": row["progress"],
         "started": row["started"], "updated": row["updated"], "steps": row["steps"][-BOARD_STEPS:]}
        for chat, row in board_own(folder).items()
    ]
    listed = {row["id"] for row in rows}
    rows += [  # in phone mode since before this PC had a board
        {"id": chat, "chat": chat, "title": entry["label"] or f"Chat {chat}", "agent": "", "machine": "", "state": "live",
         "progress": None, "started": entry["since"], "updated": entry["since"], "steps": []}
        for chat, entry in phone.items() if entry["on"] and chat not in listed
    ]
    if found.get("notes"):
        rows += note_rows(Path(str(found["notes"])), str(found.get("notesMachine", "")), str(found.get("notesAgent", "")))
    shown = []
    for row in rows:
        waits = phone.get(row.get("chat", ""), {}).get("on", False)  # a chat in phone mode may wait all day
        if row["state"] == "live" and now - row["updated"] > BOARD_QUIET_S and not waits:
            row = {**row, "state": "silent"}
        if row["state"] == "live" or now - row["updated"] < BOARD_KEPT_S:
            shown.append(row)
    shown.sort(key=lambda row: (row["state"] == "live", row["updated"]), reverse=True)
    shown, steps = shown[:most], BOARD_STEPS
    while shown and len(json.dumps(shown, ensure_ascii=False).encode("utf-8")) > bytes_max:
        if steps > 1:
            steps -= 1
            shown = [{**row, "steps": row["steps"][-steps:]} for row in shown]
        else:
            shown = shown[:-1]
    return shown


def board_wanted(folder: Path) -> bool:
    """True while the board may change with no chat in phone mode: the helper stays for it."""
    if settings(folder).get("notes"):
        return True
    now = time.time()
    return any(row["state"] == "live" and now - row["updated"] < IDLE_ON_S for row in board_own(folder).values())


def fit_copy(folder: Path, key: bytes) -> tuple[dict, str]:
    """The copy the relay keeps for a PC that is off, and the same sealed: never more than the relay takes."""
    most = STATE_BYTES
    while True:
        state = feed_answer(folder, limit=STATE_EVENTS, text_max=STATE_TEXT, bytes_max=most)
        sealed = seal(key, state)
        if len(sealed) <= RELAY_STATE_CHARS or most < 4_000:
            return state, sealed
        most //= 2


# ---------------------------------------------------------------- the inbox (phone -> PC)
def inbox(folder: Path) -> Path:
    path = folder / "inbox"
    path.mkdir(exist_ok=True)
    return path


def waiting(folder: Path, chat: str = "") -> list[tuple[Path, str]]:
    """(file, prompt id) of the prompts no chat has picked up yet, oldest first: for one chat, or all."""
    found = []
    for path in sorted(inbox(folder).glob("*.json")):
        parts = path.stem.split("-", 2)  # <time>-<chat>-<prompt id>
        if len(parts) == 3 and (not chat or parts[1] == chat):
            found.append((path, parts[2]))
    return found


def waiting_ids(folder: Path, chat: str = "") -> list[str]:
    return [prompt_id for path, prompt_id in waiting(folder, chat)]


def put_prompt(folder: Path, chat: str, prompt_id: str, text: str, image: str = "") -> None:
    path = inbox(folder) / f"{time.time_ns()}-{chat}-{prompt_id}.json"
    temporary = path.with_suffix(".tmp")
    prompt = {"id": prompt_id, "chat": chat, "t": int(time.time()), "text": text, **({"image": image} if image else {})}
    temporary.write_text(json.dumps(prompt, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def take_prompt(folder: Path, chat: str) -> dict | None:
    """The oldest prompt waiting for this chat, taken by exactly one listener."""
    for path, prompt_id in waiting(folder, chat):
        mine = path.with_suffix(f".{os.getpid()}.taken")
        try:
            path.rename(mine)
        except OSError:  # another listener was faster
            continue
        prompt = read_json(mine, None)
        mine.unlink(missing_ok=True)
        if isinstance(prompt, dict) and prompt.get("text"):
            return prompt
    return None


def keep_photo(folder: Path, prompt_id: str, ask: dict) -> dict | None:
    """Saves the photo that came with a prompt. None when it is not a JPEG of a sane size."""
    data = ask.get("img")
    if not isinstance(data, str) or not data or len(data) > PHOTO_CHARS:
        return None
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        return None
    if raw[:3] != b"\xff\xd8\xff":
        return None
    name = f"in-{prompt_id}.jpg"
    (files(folder) / name).write_bytes(raw)
    size = {}
    for side in ("w", "h"):
        try:
            size[side] = max(1, min(10_000, int(ask.get(side, 0))))
        except (TypeError, ValueError):
            pass
    return {"f": name, **size}


def answer_ask(folder: Path, key: bytes, seen: list[str], body: str, clock=time.time) -> dict:
    """What the PC says to one sealed question from the phone."""
    ask = unseal(key, body)
    if ask is None:
        return {"ok": False, "error": "key"}
    route = ask.get("route")
    try:
        after = max(0, int(ask.get("after", 0)))
    except (TypeError, ValueError):
        after = 0
    if route == "feed":
        return feed_answer(folder, after)
    if route == "board":  # All chats asks every computer, also the ones whose feed the page does not show
        return {"ok": True, "v": VERSION, "pc": int(time.time()), "chats": chat_list(folder), "board": board_rows(folder)}
    if route == "event":
        for event in read_feed(folder):
            if event["n"] == ask.get("n"):
                text = str(event.get("text", "")).encode("utf-8")[:FULL_TEXT_BYTES].decode("utf-8", "ignore")
                return {"ok": True, "event": {**event, "text": text}}
        return {"ok": False, "error": "gone"}
    if route == "image":
        for event in read_feed(folder):
            name = event.get("img", {}).get("f", "") if event["n"] == ask.get("n") and isinstance(event.get("img"), dict) else ""
            if name and re.fullmatch(r"[A-Za-z0-9_.-]+\.jpg", name):
                try:
                    raw = (files(folder) / name).read_bytes()
                except OSError:
                    break
                return {"ok": True, "n": event["n"], "type": "image/jpeg", "data": base64.b64encode(raw).decode("ascii")}
        return {"ok": False, "error": "gone"}
    if route == "prompt":
        with_photo = bool(ask.get("img"))
        text = str(ask.get("text", "")).strip() or ("(photo)" if with_photo else "")
        prompt_id = str(ask.get("pid", ""))
        chat, why = pick_chat(folder, str(ask.get("chat", "")))
        try:
            age = abs(clock() - float(ask.get("t", 0)) / 1000)
        except (TypeError, ValueError):
            age = PROMPT_MAX_AGE_S + 1
        if not text or len(text) > MAX_PROMPT or not PROMPT_ID.match(prompt_id):
            return {"ok": False, "error": "bad"}
        if prompt_id not in seen:
            if age > (PHOTO_MAX_AGE_S if with_photo else PROMPT_MAX_AGE_S):  # a recording, or one the phone gave up on (it stamps PC time)
                return {"ok": False, "error": "old"}
            if not chat or not chats(folder)[chat]["on"]:  # no chat would ever pick it up
                return {"ok": False, "error": "chat" if why == "several" else "off"}
            photo = keep_photo(folder, prompt_id, ask) if with_photo else None
            if with_photo and photo is None:
                return {"ok": False, "error": "photo"}
            seen.append(prompt_id)
            del seen[:-SEEN_KEPT]
            write_json(folder / "seen.json", seen)
            put_prompt(folder, chat, prompt_id, text, str(files(folder) / photo["f"]) if photo else "")
            add_event(folder, "prompt", text, source="phone", id=prompt_id, chat=chat, **({"img": photo} if photo else {}))
        return feed_answer(folder, after)
    return {"ok": False, "error": "route"}


# ---------------------------------------------------------------- the way to the relay
def ways_out() -> list[dict]:
    """Proxy settings to try, in order: what the environment says, what Windows is set to, straight out."""
    ways = [urllib.request.getproxies()]
    if hasattr(urllib.request, "getproxies_registry"):
        ways.append(urllib.request.getproxies_registry())
    different = []
    for way in ways:
        way = {scheme: address for scheme, address in way.items() if scheme in ("http", "https")}
        if way and way not in different:
            different.append(way)
    return [*different, {}]


class Relay:
    """Calls to one channel of the relay, on connections that stay open between calls.

    Through an office proxy a new connection takes about two seconds and a call on an open one a
    tenth of that, so the helper keeps one per way out and makes a new one when it has gone dead.
    """

    def __init__(self, found: dict):
        self.address = urllib.parse.urlsplit(found["relay"].rstrip("/") + "/c/" + found["channel"])
        self.write = found["write"]
        self.key = unb64(found["key"])
        self._kept: dict[int, tuple] = {}  # way number -> (connection, last used)
        self._good = 0  # the way that worked last is tried first
        self._context: ssl.SSLContext | None = None

    def _new(self, way: dict):
        host = self.address.hostname
        if self.address.scheme != "https":
            return http.client.HTTPConnection(host, self.address.port or 80, timeout=TIMEOUT_S)
        if self._context is None:
            self._context = ssl.create_default_context()
        port = self.address.port or 443
        proxy = urllib.parse.urlsplit(way["https"]) if way.get("https") else None
        if proxy is not None and proxy.hostname and not urllib.request.proxy_bypass(host):
            connection = http.client.HTTPSConnection(proxy.hostname, proxy.port or 80, timeout=TIMEOUT_S, context=self._context)
            connection.set_tunnel(host, port)
            return connection
        return http.client.HTTPSConnection(host, port, timeout=TIMEOUT_S, context=self._context)

    def _drop(self, index: int) -> None:
        connection = self._kept.pop(index, (None, 0))[0]
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def call(self, method: str, path: str, body: str | None = None, owner: bool = False) -> tuple[int, bytes]:
        """(status, body). Raises OSError only when no way out of this network reaches the relay."""
        headers = {"User-Agent": "vector-relay", "Content-Type": "text/plain"}
        if owner:
            headers["X-Relay-Write"] = self.write
        data = (body or "").encode("utf-8") if method in ("POST", "PUT") else None
        ways = ways_out()
        start = self._good if self._good < len(ways) else 0
        failure: OSError = OSError("no way out")
        for index in [start, *(number for number in range(len(ways)) if number != start)]:
            for fresh in (False, True):
                kept = self._kept.get(index)
                if fresh:
                    self._drop(index)
                    connection = self._new(ways[index])
                elif kept is not None and time.monotonic() - kept[1] < KEEP_IDLE_S:
                    connection = kept[0]
                else:
                    continue
                try:
                    connection.request(method, self.address.path + path, body=data, headers=headers)
                    reply = connection.getresponse()
                    answer = reply.read()
                except (OSError, http.client.HTTPException) as error:
                    self._kept.pop(index, None)
                    try:
                        connection.close()
                    except OSError:
                        pass
                    failure = error if isinstance(error, OSError) else OSError(str(error) or type(error).__name__)
                    continue
                if reply.will_close:
                    self._kept.pop(index, None)
                    connection.close()
                else:
                    self._kept[index] = (connection, time.monotonic())
                if reply.status == 407:  # this proxy wants a password, so it will not carry this
                    failure = OSError("the proxy wants a password")
                    break
                self._good = index
                return reply.status, answer
        raise failure

    def close(self) -> None:
        for index in list(self._kept):
            self._drop(index)


# ---------------------------------------------------------------- the helper
def guard_port(folder: Path) -> int:
    """A local port only one helper for this folder can hold: how the others know it is running."""
    return 47800 + zlib.crc32(str(folder.resolve()).lower().encode("utf-8")) % 150


def local_name() -> str:
    """The name this PC answers to on its own network, which stays when its address changes."""
    name = socket.gethostname().split(".")[0]
    if sys.platform == "darwin":
        try:
            asked = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5)
            name = asked.stdout.strip() or name
        except (OSError, subprocess.SubprocessError):
            pass
    return name + ".local"


def helper_running(folder: Path) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", guard_port(folder)), timeout=0.5):
            return True
    except OSError:
        return False


def start_alone(folder: Path, name: str) -> None:
    """Starts one of this file's commands so that it is still there when the chat's command has ended."""
    command = [sys.executable, str(Path(__file__).resolve()), name]
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "env": {**os.environ, "VECTOR_RELAY_HOME": str(folder)}, "cwd": str(folder)}
    if os.name == "nt":
        # no console, its own group, normal priority (a job started from a chat runs below normal),
        # and outside the chat's job
        flags = 0x00000008 | 0x00000200 | 0x00000020
        try:
            subprocess.Popen(command, creationflags=flags | 0x01000000, **options)
        except OSError:
            subprocess.Popen(command, creationflags=flags, **options)
    else:
        subprocess.Popen(command, start_new_session=True, **options)


def start_helper(folder: Path) -> bool:
    """Starts the helper unless one runs. True when one is running afterwards."""
    if not paired(settings(folder)):
        return False
    if helper_running(folder):
        return True
    start_alone(folder, "serve")
    for attempt in range(30):
        if helper_running(folder):
            return True
        time.sleep(0.1)
    return False


def log(folder: Path, text: str) -> None:
    path = folder / "serve.log"
    try:
        if path.stat().st_size > 200_000:
            path.write_text("", encoding="utf-8")
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as lines:
        lines.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")


def view_port(folder: Path) -> int:
    """Where the helper hands the page to this PC's own browser (`view`)."""
    return 48800 + zlib.crc32(str(folder.resolve()).lower().encode("utf-8")) % 150


class PageOnly(BaseHTTPRequestHandler):
    """The files of the page, for a browser on this PC. No data: that comes sealed from the relay."""

    protocol_version = "HTTP/1.1"
    timeout = 75

    def log_message(self, *arguments) -> None:
        pass

    def do_GET(self) -> None:
        file = PAGE_FILES.get(urllib.parse.urlsplit(self.path).path.lstrip("/"))
        try:
            data, code = ((DOCS / file).read_bytes(), 200) if file else (b"Not found.", 404)
        except OSError:
            data, code = b"Not found.", 404
        self.send_response(code)
        self.send_header("Content-Type", PAGE_TYPES[Path(file).suffix] if code == 200 else "text/plain")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve_page(folder: Path) -> ThreadingHTTPServer | None:
    """The page on a port only this PC can reach. None when the port is taken: `view` then opens the page at the relay."""
    try:
        server = ThreadingHTTPServer(("127.0.0.1", view_port(folder)), PageOnly)
    except OSError as error:
        log(folder, f"view: port {view_port(folder)} is taken ({error})")
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def serve(folder: Path) -> int:
    """Passes questions and answers between the relay and this folder until nobody needs it."""
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # macOS and Linux keep a closed port busy for half a minute; this lets a new helper start
        # at once and still refuses a second one while the first listens
        guard.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        guard.bind(("127.0.0.1", guard_port(folder)))
        guard.listen(8)
    except OSError:
        return 0  # one is running already

    def greet() -> None:
        while True:
            try:
                guard.accept()[0].close()
            except OSError:
                return

    threading.Thread(target=greet, daemon=True).start()
    found = settings(folder)
    if not paired(found):
        return 2
    if found.get("local"):
        return serve_local(folder, found, guard)
    relay = Relay(found)
    page = serve_page(folder)
    seen = read_json(folder / "seen.json", [])
    seen = [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else []
    stop = folder / "stop"
    stop.unlink(missing_ok=True)
    status = {"pid": os.getpid(), "started": int(time.time()), "contact": 0, "error": ""}
    published_at, published, last_ask, said = 0.0, None, 0.0, ""
    log(folder, f"helper {VERSION} started, pid {os.getpid()}")

    held_now = threading.Event()  # set while the helper sits in the request the relay holds

    def end_when_told() -> None:
        """`stop` must not wait up to TAKE_HOLD_S for that request: the helper ends in the middle of it."""
        while not (stop.exists() and held_now.is_set()):
            time.sleep(0.2)
        log(folder, "helper stopped")
        stop.unlink(missing_ok=True)
        guard.close()
        os._exit(0)  # nothing is half-written here: the main thread only waits for the relay

    threading.Thread(target=end_when_told, daemon=True).start()

    def note(error: str) -> None:
        nonlocal said
        status["error"] = error
        if error != said:
            said = error
            log(folder, error or "relay reached")
        write_json(folder / "serve.json", status)

    try:
        while not stop.exists():
            now = time.time()
            idle = now - max(last_active(folder), status["started"])
            if (idle > (IDLE_ON_S if mode(folder)["on"] else IDLE_OFF_S) and not listening(folder)
                    and not board_wanted(folder)):
                log(folder, "nobody needs the helper any more")
                break
            try:
                state = feed_answer(folder, limit=STATE_EVENTS, text_max=STATE_TEXT, bytes_max=STATE_BYTES)
                shown = (state["last"], json.dumps(state["chats"], sort_keys=True), len(state["waiting"]),
                         json.dumps(state["board"], sort_keys=True))
                due = shown != published and now - published_at >= PUBLISH_GAP_S
                if published is None or due or now - published_at >= HEARTBEAT_S:
                    code, _ = relay.call("PUT", "/state", fit_copy(folder, relay.key)[1], owner=True)
                    if code == 403:
                        note("the relay says this channel belongs to another PC: run `vector_relay.py forget`, then pair again")
                        time.sleep(ASK_RETRY_S)
                        continue
                    if code != 200:
                        raise OSError(f"the relay answered {code} to the state")
                    published_at, published = now, shown
                asked = time.monotonic()
                held_now.set()
                try:
                    code, answer = relay.call("POST", f"/asks/take?wait={TAKE_HOLD_S}", owner=True)
                finally:
                    held_now.clear()
                held = time.monotonic() - asked >= 1  # the relay kept the request open: no need to wait before the next
                if code == 404:  # the relay forgot this channel: claim it again
                    published = None
                    time.sleep(2)
                    continue
                if code != 200:
                    raise OSError(f"the relay answered {code}")
                now = time.time()
                status["contact"] = int(now)
                if status["error"] or not said:
                    note("")
                asks = json.loads(answer.decode("utf-8")).get("asks", [])
                for ask in asks:
                    reply = answer_ask(folder, relay.key, seen, str(ask.get("body", "")))
                    relay.call("PUT", "/answer/" + str(ask.get("id", "")), seal(relay.key, reply), owner=True)
                    last_ask = now
                write_json(folder / "serve.json", status)
            except OSError as error:
                note(f"cannot reach the relay: {error}")
                time.sleep(ASK_RETRY_S if now - last_ask > ASK_BUSY_FOR_S else 3)
                continue
            except Exception as error:  # a broken answer must not end the helper
                note(f"{type(error).__name__}: {error}")
                time.sleep(5)
                continue
            if asks or held:
                continue
            since = now - last_ask
            time.sleep(ASK_BUSY_S if since < ASK_BUSY_FOR_S else ASK_RECENT_S if since < ASK_RECENT_FOR_S else ASK_IDLE_S)
    finally:
        relay.close()
        if page is not None:
            page.server_close()
        stop.unlink(missing_ok=True)
        guard.close()
        log(folder, "helper stopped")
    return 0


class LocalServer(ThreadingHTTPServer):
    """Listens on every address of this PC, old style and new, so `<name>.local` works whichever the phone picks."""

    daemon_threads = True
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()

    def handle_error(self, request, client_address) -> None:
        pass  # a phone that went to sleep in the middle of a request


def open_local_server(port: int, handler) -> ThreadingHTTPServer:
    try:
        return LocalServer(("::", port), handler)
    except OSError:  # a PC with no IPv6
        plain = type("PlainLocalServer", (ThreadingHTTPServer,), {"daemon_threads": True, "handle_error": LocalServer.handle_error})
        return plain(("0.0.0.0", port), handler)


def serve_local(folder: Path, found: dict, guard: socket.socket) -> int:
    """Local mode: this PC is the page and the relay in one, for a phone on the same Wi-Fi.

    Same paths as the relay, so the phone page does not know the difference; a question is
    answered the moment it comes, because the feed is right here.
    """
    key, channel, port = unb64(found["key"]), str(found["channel"]), int(found["local"])
    seen = read_json(folder / "seen.json", [])
    seen = [item for item in seen if isinstance(item, str)] if isinstance(seen, list) else []
    stop = folder / "stop"
    stop.unlink(missing_ok=True)
    status = {"pid": os.getpid(), "started": int(time.time()), "contact": 0, "error": "", "local": port}
    one_at_a_time = threading.Lock()
    answers: dict[str, tuple[float, str]] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 75  # a connection the phone left open and forgot

        def log_message(self, *arguments) -> None:
            pass

        def reply(self, code: int, body: bytes | str = b"", kind: str = "text/plain", keep: str = "no-store") -> None:
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", keep)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if code != 204 and self.command != "HEAD":
                self.wfile.write(data)

        def page(self, name: str) -> None:
            file = PAGE_FILES.get(name)
            if file is None:
                return self.reply(404, "Not found.")
            try:
                data = (DOCS / file).read_bytes()
            except OSError:
                return self.reply(404, "Not found.")
            self.reply(200, data, PAGE_TYPES[Path(file).suffix], "no-cache")

        def handle_one(self, method: str) -> None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_ASK_BYTES:
                self.close_connection = True
                return self.reply(413, "Too large.")
            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            parts = [part for part in urllib.parse.urlsplit(self.path).path.split("/") if part]
            if not parts or parts[0] != "c":
                if method != "GET" or len(parts) > 1:
                    return self.reply(404, "Not found.")
                return self.page(parts[0] if parts else "")
            if len(parts) < 3 or not secrets.compare_digest(parts[1].encode("utf-8"), channel.encode("utf-8")):
                return self.reply(404, "Nothing here.")
            what, ask_id = parts[2], parts[3] if len(parts) == 4 else ""
            if what == "state" and len(parts) == 3 and method == "GET":
                with one_at_a_time:
                    state = feed_answer(folder, limit=STATE_EVENTS, text_max=STATE_TEXT, bytes_max=STATE_BYTES)
                return self.reply(200, seal(key, state))
            if not PROMPT_ID.match(ask_id):
                return self.reply(404, "Not found.")
            if what == "ask" and method == "POST":
                if not body:
                    return self.reply(413, "Too large.")
                with one_at_a_time:
                    now = time.time()
                    for old in [name for name, (made, _) in answers.items() if now - made > ANSWER_KEPT_S]:
                        del answers[old]
                    if len(answers) >= ANSWERS_KEPT:
                        return self.reply(429, "Too many questions nobody came back for.")
                    try:
                        answer = answer_ask(folder, key, seen, body)
                    except Exception as error:  # a broken question must not end the helper
                        log(folder, f"{type(error).__name__}: {error}")
                        return self.reply(500, "The PC could not answer that.")
                    answers[ask_id] = (now, seal(key, answer))
                    if answer.get("ok"):
                        status["contact"] = int(now)
                return self.reply(200, "ok")
            if what == "answer" and method == "GET":
                with one_at_a_time:
                    kept = answers.pop(ask_id, None)
                return self.reply(204) if kept is None else self.reply(200, kept[1])
            return self.reply(404, "Not found.")

        def do_GET(self) -> None:
            self.handle_one("GET")

        def do_POST(self) -> None:
            self.handle_one("POST")

    try:
        server = open_local_server(port, Handler)
    except OSError as error:
        status["error"] = f"port {port} on this PC is taken ({error}): pair again with --port <another>"
        log(folder, status["error"])
        write_json(folder / "serve.json", status)
        guard.close()
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(folder, f"helper {VERSION} started in local mode on port {port}, pid {os.getpid()}")
    written = None
    try:
        while not stop.exists():
            now = time.time()
            idle = now - max(last_active(folder), status["started"])
            if (idle > (IDLE_ON_S if mode(folder)["on"] else IDLE_OFF_S) and not listening(folder)
                    and not board_wanted(folder)):
                log(folder, "nobody needs the helper any more")
                break
            if written != status["contact"] // 5:
                written = status["contact"] // 5
                write_json(folder / "serve.json", status)
            time.sleep(0.3)
    finally:
        server.shutdown()
        server.server_close()
        stop.unlink(missing_ok=True)
        guard.close()
        log(folder, "helper stopped")
    return 0


# ---------------------------------------------------------------- a 4-digit code instead of the QR code
# How 4 digits can stand in for a 256-bit key.
#
# Both sides turn the code into a number, raise it to a secret of their own and swap the results
# (SPEKE, in the 2048-bit group of RFC 3526). Only two sides that started from the same code end
# with the same key. What crosses the relay says nothing about the code: nobody can try the 10,000
# codes at home, and each guess has to be sent to this PC, which takes CODE_TRIES of them. The link
# with the real key then travels sealed with the key the two just made.
#
# The swap happens in a "room": a channel of the relay whose name comes from the time alone, so the
# name gives the code away to nobody. One PC at a time can hold it.
CODE_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)


def code_room(moment: float) -> str:
    """The channel where a code is used in the ROOM_EVERY_S that hold this moment."""
    named = hashlib.sha256(f"vector-relay pair room v1|{int(moment // ROOM_EVERY_S)}".encode("ascii")).digest()
    return "pair" + b64(named)[:28]


def code_base(code: str) -> int:
    """The code as a member of the group: 320 hashed bytes, squared so that it lands in the large half."""
    wide = b"".join(hashlib.sha256(b"vector-relay pair v1|" + code.encode("utf-8") + bytes([count])).digest() for count in range(10))
    return pow(int.from_bytes(wide, "big") % CODE_PRIME, 2, CODE_PRIME)


def code_public(code: str, secret: int) -> int:
    """What one side sends the other."""
    return pow(code_base(code), secret, CODE_PRIME)


def code_keys(secret: int, theirs: int, pc: int, phone: int) -> tuple[bytes, bytes] | None:
    """(the proof the phone sends, the key that seals the link), or None for a number no honest side sends."""
    if not 1 < theirs < CODE_PRIME - 1:
        return None
    shared = pow(theirs * theirs % CODE_PRIME, secret, CODE_PRIME)
    key = hashlib.sha256(b"vector-relay pair v1 key|" + b"".join(number.to_bytes(256, "big") for number in (pc, phone, shared))).digest()
    return hmac.new(key, b"proof", "sha256").digest(), hmac.new(key, b"seal", "sha256").digest()


def room_relay(found: dict, kept: dict) -> Relay:
    return Relay({"relay": found["relay"], "channel": kept["channel"], "write": kept["write"], "key": b64(bytes(32))})


def end_code(folder: Path) -> None:
    """A code that still waits stops working: its room is wiped, and the process that waits there ends."""
    kept, found = read_json(folder / "pairing.json", {}), settings(folder)
    if kept.get("state") == "waiting" and found.get("relay") and not found.get("local"):
        relay = room_relay(found, kept)
        try:
            relay.call("DELETE", "", owner=True)
        except OSError:
            pass  # the room is gone by itself when the code runs out
        relay.close()
    (folder / "pairing.json").unlink(missing_ok=True)


def offer_code(folder: Path) -> int:
    """Makes a code, takes the room and leaves a process there that waits for the phone."""
    found = settings(folder)
    end_code(folder)
    code, secret, started = f"{secrets.randbelow(10_000):04d}", 2 + secrets.randbits(256), time.time()
    public = code_public(code, secret).to_bytes(256, "big")
    while True:
        now = time.time()
        kept = {"state": "waiting", "code": code, "secret": hex(secret), "made": int(now),
                "channel": code_room(now), "write": secrets.token_urlsafe(24)}
        relay = room_relay(found, kept)
        try:
            status, _ = relay.call("PUT", "/state", json.dumps({"v": 1, "a": b64(public), "t": int(now * 1000)}), owner=True)
        except OSError as error:
            print(f"No code: cannot reach the relay ({error}). The QR code still works:  {ME} pair --open")
            return 5
        finally:
            relay.close()
        # The room is kept CODE_GRACE_S after a code was used. Two computers added one after the
        # other meet there, so the second waits that long for the room before it gives up.
        if status != 403 or now - started > CODE_GRACE_S + 5:
            break
        time.sleep(2)
    if status == 403:
        print("No code: another computer is showing a code on this relay right now. Finish that one, or try again in a few minutes.")
        return 5
    if status != 200:
        print(f"No code: the relay answered {status}. The QR code still works:  {ME} pair --open")
        return 5
    write_json(folder / "pairing.json", kept)
    start_alone(folder, "pair-wait")
    print(f"Code for the phone: {code}")
    print(f"On the phone: Device, + Add device, a name, these 4 digits. It links one phone, within {CODE_VALID_S // 60} minutes.")
    return 0


def show_to_phone(folder: Path) -> None:
    """A phone that was just linked finds this computer at the relay, also while no chat is in phone mode."""
    found = settings(folder)
    if helper_running(folder) or not paired(found) or found.get("local"):
        return  # the helper keeps the relay's copy fresh by itself
    relay = Relay(found)
    try:
        status, _ = relay.call("PUT", "/state", fit_copy(folder, relay.key)[1], owner=True)
        if status != 200:
            log(folder, f"code: the relay answered {status} to this computer's state")
    except OSError as error:
        log(folder, f"code: this computer's state did not reach the relay ({error})")
    finally:
        relay.close()


def wait_for_code(folder: Path) -> int:
    """Waits in the room for a phone that knows the code, hands it the link, and wipes the room."""
    kept, found = read_json(folder / "pairing.json", {}), settings(folder)
    if kept.get("state") != "waiting" or not paired(found):
        return 0
    relay = room_relay(found, kept)
    secret = int(kept["secret"], 16)
    mine = code_public(kept["code"], secret)
    tries, ended = CODE_TRIES, "expired"

    def answer(ask: dict, reply: dict) -> None:
        relay.call("PUT", "/answer/" + str(ask.get("id", "")), json.dumps(reply), owner=True)

    try:
        while ended == "expired" and time.time() < kept["made"] + CODE_VALID_S:
            left = kept["made"] + CODE_VALID_S - time.time()
            try:
                status, body = relay.call("POST", f"/asks/take?wait={int(max(1, min(TAKE_HOLD_S, left)))}", owner=True)
                if status == 404:  # a newer code, `forget`, or another PC's room by now
                    ended = "ended"
                    break
                if status != 200:
                    raise OSError(f"the relay answered {status}")
                asks = json.loads(body.decode("utf-8")).get("asks", [])
                for ask in asks:
                    try:
                        said = json.loads(str(ask.get("body", ""))[:2000])
                        theirs, proof = int.from_bytes(unb64(said["b"]), "big"), unb64(said["m"])
                        keys = code_keys(secret, theirs, mine, theirs)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        keys, proof = None, b""
                    if keys is not None and hmac.compare_digest(keys[0], proof):
                        found = settings(folder)
                        link = found.get("page", found["relay"] + "/") + "#r=" + ticket(found)
                        show_to_phone(folder)
                        answer(ask, {"ok": True, "link": seal(keys[1], {"link": link})})
                        ended = "linked"
                        break
                    tries -= 1
                    answer(ask, {"ok": False, "left": tries})
                    if tries <= 0:
                        ended = "wrong"
                        break
                if not asks:
                    time.sleep(0.2)  # a relay that does not hold the request must not be asked in a tight loop
            except (OSError, ValueError) as error:
                log(folder, f"code: {error}")
                time.sleep(3)
        if read_json(folder / "pairing.json", {}).get("write") == kept["write"]:  # not a newer code's file
            write_json(folder / "pairing.json", {"state": ended, "ended": int(time.time()), "write": kept["write"]})
        log(folder, f"code: {ended}")
        if ended in ("linked", "wrong"):
            time.sleep(CODE_GRACE_S)
    finally:
        try:
            relay.call("DELETE", "", owner=True)
        except OSError:
            pass
        relay.close()
    return 0


CODE_ENDED = {
    "linked": "a phone was linked with it",
    "wrong": f"it was answered wrong {CODE_TRIES} times, so it is dead: make a new one",
    "expired": "it ran out before a phone used it",
    "ended": "it was replaced",
}


def code_line(folder: Path) -> str:
    """What became of the last code, for `status`. "" when there never was one."""
    kept = read_json(folder / "pairing.json", {})
    if kept.get("state") == "waiting":
        left = kept.get("made", 0) + CODE_VALID_S - time.time()
        return f"{kept.get('code', '')}, good for {max(1, round(left / 60))} more min" if left > 0 else CODE_ENDED["expired"]
    if kept.get("state") in CODE_ENDED:
        return f"{CODE_ENDED[kept['state']]} ({time.strftime('%H:%M', time.localtime(kept.get('ended', 0)))})"
    return ""


# ---------------------------------------------------------------- commands
ME = "vector_relay.py"
SEVERAL = "Several chats are in phone mode. Say which one with --chat <id>:"


def say_chats(folder: Path) -> None:
    for chat, entry in chats(folder).items():
        if entry["on"]:
            print(f"  {chat}  {entry['label'] or '(no name)'}")


def command_pair(folder: Path, relay: str, page: str, show_link: bool, open_picture: bool,
                 local: bool = False, port: int = LOCAL_PORT, host: str = "", code: bool = False,
                 no_code: bool = False) -> int:
    found = settings(folder)
    if no_code:
        waited = read_json(folder / "pairing.json", {}).get("state") == "waiting"
        end_code(folder)
        print("The code is off. It links no phone any more." if waited else "No code was waiting for a phone.")
        return 0
    if code and (local or (found.get("local") and not relay)):
        print(f"A code needs the relay. In local mode the phone scans the QR code:  {ME} pair --local --open")
        return 2
    if local:
        address = f"http://{host or local_name()}:{port}"
        if not RELAY_ADDRESS.match(address) or not 1024 <= port <= 65535:
            print("Local mode needs a name ending in .local or a private address (192.168.x.x, 10.x.x.x), and a port above 1023.")
            return 2
        if not found.get("local"):  # the key that went through a relay's address is not used here
            for name in ("channel", "key", "write"):
                found.pop(name, None)
        found.update(relay=address, page=address + "/", local=port)
    elif relay:
        relay = relay.rstrip("/")
        if not RELAY_ADDRESS.match(relay):
            print("The relay address must look like https://<name>.pages.dev")
            return 2
        if relay != found.get("relay"):
            found.pop("page", None)
        found["relay"] = relay
        found.pop("local", None)
    found.setdefault("relay", RELAY)
    if page:
        found["page"] = page
    found.setdefault("page", found["relay"] + "/")
    if not paired(found):
        found.update(channel=secrets.token_urlsafe(16), key=b64(os.urandom(32)), write=secrets.token_urlsafe(24))
    write_json(folder / "settings.json", found)
    link = found["page"] + "#r=" + ticket(found)
    picture = folder / "pair.png"
    if code and not open_picture and not show_link:
        return offer_code(folder)  # he asked for 4 digits: nothing about the QR code to read past
    try:
        import segno

        segno.make(link, error="m").save(str(picture), scale=8, border=3)
        print(f"QR code: {picture}")
        print("Scan it with the phone's camera. It holds the key: do not share the picture.")
        if open_picture and hasattr(os, "startfile"):
            os.startfile(picture)  # noqa: S606 - the user's own picture viewer
        elif open_picture and sys.platform == "darwin":
            subprocess.run(["open", str(picture)], check=False)
    except ImportError:
        print("No QR picture (pip install segno). Use --show-link and open the link on the phone.")
    if found.get("local"):
        print(f"Local mode: the phone must be on the same Wi-Fi as this PC, which serves the page on port {found['local']}.")
        if helper_running(folder):
            print(f"The helper still runs with the old settings. Run:  {ME} stop")
    if show_link:
        print(link)
    return offer_code(folder) if code else 0


def command_on(folder: Path, label: str, named: str) -> int:
    if not paired(settings(folder)):
        print(f"Not linked to a phone yet. Run:  {ME} pair --open")
        return 2
    now = int(time.time())
    with locked(folder):
        found = chats(folder)
        found = {chat: entry for chat, entry in found.items() if entry["on"] or now - entry["since"] < 7 * 24 * 3600}
        chat = named if named in found else ""
        while not chat or (chat in found and chat != named):
            chat = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(4))
        was_on = found.get(chat, {}).get("on", False)
        label = label or found.get(chat, {}).get("label", "")
        found[chat] = {"label": label, "on": True, "since": now}
        write_json(folder / "chats.json", found)
    touch(folder)
    board_step(folder, chat, title=label, own=False)  # a chat in phone mode is on the board without asking
    if not was_on:
        add_event(folder, "note", "Phone mode on" + (f": {label}" if label else ""), chat=chat)
    for old in files(folder).glob("*"):
        try:
            if now - old.stat().st_mtime > FILES_KEPT_S:
                old.unlink()
        except OSError:
            pass
    running = start_helper(folder)
    print(f"Phone mode is on. chat={chat}" + ("" if running else f"  The helper did not start: see `{ME} status`."))
    return 0 if running else 1


def command_off(folder: Path, named: str, everything: bool) -> int:
    on = chats_on(folder)
    if everything or not on:
        leaving = on
    else:
        chat, why = pick_chat(folder, named)
        if not chat:
            print(SEVERAL if why == "several" else f"No chat {named}.")
            say_chats(folder)
            return 2
        leaving = [chat] if chat in on else []
    with locked(folder):
        found = chats(folder)
        for chat in leaving:
            found[chat].update(on=False, since=int(time.time()))
        write_json(folder / "chats.json", found)
    for chat in leaving:
        add_event(folder, "note", "Phone mode off", chat=chat)
        if not board_own(folder).get(chat, {"own": True})["own"]:  # only phone mode put it on the board
            board_step(folder, chat, "Left phone mode", end="done", own=False)
    if leaving:
        touch(folder)  # the helper stays a few minutes so the phone sees this
    left = len(chats_on(folder))
    print("Phone mode is off." + (f" {left} other chat(s) still on." if left else ""))
    return 0


def command_say(folder: Path, kind: str, words: list[str], file: str, image: str, named: str) -> int:
    if file == "-":
        text = sys.stdin.read()
    elif file:
        text = Path(file).read_text(encoding="utf-8-sig")
    else:
        text = " ".join(words)
    text = text.strip()
    if not text and not image:
        print("Nothing to say.")
        return 2
    chat, why = pick_chat(folder, named)
    if why in ("several", "unknown"):
        print(SEVERAL if why == "several" else f"No chat {named}.")
        say_chats(folder)
        return 2
    more = {"chat": chat} if chat else {}
    if image:
        try:
            more["img"] = make_picture(Path(image), folder)
        except ImportError:
            print("Pictures need Pillow:  pip install pillow")
            return 2
        except (OSError, ValueError) as error:
            print(f"Could not read the picture: {error}")
            return 2
    event = add_event(folder, kind, text, **more)
    touch(folder)
    if chat and kind in ("update", "result", "error"):  # what it says to the phone is its step on the board
        first = next((line.strip(" #*") for line in text.splitlines() if line.strip(" #*")), "Posted a picture" if image else "")
        board_step(folder, chat, {"result": "Result: ", "error": "Needs you: "}.get(kind, "") + first, own=False)
    on = bool(chat) and chats(folder)[chat]["on"]
    if on:
        start_helper(folder)
    print(f"ok n={event['n']}" + ("" if on else " (phone mode is off: the phone sees this when it is on)"))
    return 0


def command_listen(folder: Path, wait: float, named: str) -> int:
    chat, why = pick_chat(folder, named)
    if why == "several":
        print("OFF " + SEVERAL)
        say_chats(folder)
        return 2
    if not chat or not chats(folder)[chat]["on"]:
        print(f"OFF phone mode is off. Run:  {ME} on")
        return 3
    start_helper(folder)
    beat, me = folder / f"listening-{chat}", str(os.getpid())
    beat.write_text(me, encoding="ascii")  # a listener that was left behind for this chat sees this and ends
    print("LISTENING", flush=True)
    started, last_beat, last_look = time.time(), time.time(), time.time()
    try:
        while True:
            now = time.time()
            try:
                mine = beat.read_text(encoding="ascii") == me
            except OSError:
                mine = True
            if not mine:  # a newer `listen` took over: the prompt must reach the one the chat reads
                print("REPLACED", flush=True)
                return 4
            if now - last_beat >= LISTEN_BEAT_S:
                beat.write_text(me, encoding="ascii")
                touch(folder)
                last_beat = now
            prompt = take_prompt(folder, chat)
            if prompt is not None:
                at = time.strftime("%H:%M", time.localtime(prompt.get("t", now)))
                print(f"PROMPT id={prompt.get('id', '')} at={at} chat={chat}")
                if prompt.get("image"):
                    print(f"IMAGE {prompt['image']}")
                print(prompt["text"], flush=True)
                return 0
            if now - last_look >= 2:
                last_look = now
                if not chats(folder).get(chat, {}).get("on"):
                    print("OFF", flush=True)
                    return 3
                if not helper_running(folder):
                    start_helper(folder)
            if wait and now - started >= wait:
                print("NONE", flush=True)
                return 0
            time.sleep(0.4)
    finally:
        try:
            if beat.read_text(encoding="ascii") == me:
                beat.unlink()
        except OSError:
            pass


def command_step(folder: Path, words: list[str], named: str, title: str, agent: str,
                 progress: int | None, end: str) -> int:
    text = " ".join(words).strip()
    known = {**chats(folder), **board_own(folder)}
    if named and named not in known:
        print(f"No chat {named}. A chat that is not on the board yet starts with:  {ME} step --title \"what it is about\" \"first step\"")
        return 2
    chat = named
    if not chat and title:  # a new row
        while not chat or chat in known:
            chat = "".join(secrets.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(4))
    elif not chat:
        chat, why = pick_chat(folder)
        if not chat:
            print("Say which chat with --chat <id>." if why == "several" else
                  f"Which chat? Start its row with:  {ME} step --title \"what it is about\" \"first step\"")
            if why == "several":
                say_chats(folder)
            return 2
    if not text and not title and progress is None and not end:
        print("Nothing to say.")
        return 2
    board_step(folder, chat, text, title, agent, progress, end)
    touch(folder)
    if not paired(settings(folder)):
        print(f"ok chat={chat}  (no phone or page is linked yet: `{ME} pair --open`, or `{ME} board` shows it here)")
        return 0
    running = start_helper(folder)
    print(f"ok chat={chat}" + ("" if running else f"  The helper did not start: see `{ME} status`."))
    return 0


def command_board(folder: Path, notes: str, no_notes: bool, machine: str, agent: str) -> int:
    found = settings(folder)
    if no_notes:
        for name in ("notes", "notesMachine", "notesAgent"):
            found.pop(name, None)
        write_json(folder / "settings.json", found)
        print("The board no longer reads a folder of notes.")
        return 0
    if notes:
        place = Path(notes).expanduser().resolve()
        if not place.is_dir():
            print(f"No such folder: {place}")
            return 2
        found.update(notes=str(place), notesMachine=machine, notesAgent=agent)
        write_json(folder / "settings.json", found)
        touch(folder)
        if paired(found):
            start_helper(folder)
    rows, now = board_rows(folder, most=10_000, bytes_max=10_000_000), time.time()
    running = [row for row in rows if row["state"] == "live"]
    print(f"Board: {len(running)} running, {len(rows) - len(running)} ended in the last {BOARD_KEPT_S // 86400} days")
    if found.get("notes"):
        only = ", ".join(f"{name} {found[key]}" for name, key in (("computer", "notesMachine"), ("agent", "notesAgent")) if found.get(key))
        print(f"Notes: {found['notes']}" + (f"  (only {only})" if only else ""))
    for row in rows[:BOARD_ROWS]:
        today = time.localtime(row["updated"])[:3] == time.localtime(now)[:3]
        at = time.strftime("%H:%M " if today else "%d %b", time.localtime(row["updated"]))
        state = "running" + (f" {row['progress']}%" if row["progress"] is not None else "") if row["state"] == "live" else row["state"]
        who = " · ".join(part for part in (row["machine"], row["agent"]) if part)
        print(f"  {at}  {state:<12} {row['title']}" + (f"  [{who}]" if who else ""))
        if row["steps"]:
            print(f"         {time.strftime('%H:%M', time.localtime(row['steps'][-1][0]))} {row['steps'][-1][1]}")
    if len(rows) > BOARD_ROWS:
        print(f"  ... and {len(rows) - BOARD_ROWS} older. The page shows the first {BOARD_ROWS} too.")
    return 0


def command_view(folder: Path) -> int:
    """Opens the page on this PC, linked to this PC already: no phone, no code."""
    found = settings(folder)
    if not paired(found):
        print(f"Not linked yet. Run:  {ME} pair --open")
        return 2
    address = found.get("page", found["relay"] + "/")
    if start_helper(folder) and not found.get("local"):
        for attempt in range(30):  # the helper hands out the page itself, the same version as this file
            try:
                with socket.create_connection(("127.0.0.1", view_port(folder)), timeout=0.3):
                    address = f"http://127.0.0.1:{view_port(folder)}/"
                    break
            except OSError:
                time.sleep(0.1)
    import webbrowser

    opened = webbrowser.open(address + "?view=chats#r=" + ticket(found))
    where = "from this PC" if address.startswith("http://127.0.0.1") else f"at {urllib.parse.urlsplit(address).netloc}"
    print(f"All chats is open in your browser ({where})." if opened else "No browser could be opened on this PC.")
    print("Another computer: there, `pair --code`; here on the page, Device, + Add device, its 4 digits.")
    return 0 if opened else 1


def command_status(folder: Path) -> int:
    found, helper = settings(folder), read_json(folder / "serve.json", {})
    running = helper_running(folder)
    events = read_feed(folder)
    print(f"Vector Relay {VERSION}   data: {folder}")
    if not paired(found):
        print(f"Not linked to a phone yet. Run:  {ME} pair --open")
        return 0
    if found.get("local"):
        print("Mode:       local, no relay: the phone must be on this PC's Wi-Fi")
    else:
        print(f"Relay:      {urllib.parse.urlsplit(found['relay']).netloc}")
    print(f"Phone page: {found.get('page', found['relay'] + '/')}")
    if code_line(folder):
        print(f"Code:       {code_line(folder)}")
    on = chats_on(folder)
    print(f"Phone mode: {'on in ' + str(len(on)) + ' chat(s)' if on else 'off'}")
    for chat, entry in chats(folder).items():
        if entry["on"]:
            state = "waiting for a prompt" if listening(folder, chat) else "working (not listening right now)"
            print(f"  {chat}  {entry['label'] or '(no name)'}: {state}, {len(waiting_ids(folder, chat))} prompt(s) waiting")
    print(f"Helper:     {'running' if running else 'not running'}")
    if running and helper.get("contact"):
        what = "Phone last asked:" if found.get("local") else "Relay last reached:"
        print(f"{what} {time.strftime('%H:%M:%S', time.localtime(helper['contact']))}")
    elif running and found.get("local"):
        print("Phone last asked: not yet")
    if running and helper.get("error"):
        print(f"Trouble:    {helper['error']}")
    print(f"Feed:       {len(events)} events")
    rows = board_rows(folder, most=10_000, bytes_max=10_000_000)
    print(f"Board:      {sum(row['state'] == 'live' for row in rows)} running, {len(rows)} in all"
          + (f"  (with the notes in {found['notes']})" if found.get("notes") else "") + f"   `{ME} board` lists them")
    return 0


def command_stop(folder: Path) -> int:
    if helper_running(folder):
        (folder / "stop").write_text("", encoding="ascii")
        for attempt in range(400):
            if not helper_running(folder):
                break
            time.sleep(0.1)
    print("The helper is " + ("still running." if helper_running(folder) else "stopped."))
    return 0


def command_forget(folder: Path) -> int:
    """Unlinks every phone: wipes this channel at the relay, the key, the feed and the pictures. The relay address stays."""
    found = settings(folder)
    command_stop(folder)
    end_code(folder)
    if paired(found) and found.get("local"):
        print("The old key is gone: the phone that had it is no longer linked.")
    elif paired(found):
        relay = Relay(found)
        try:
            code, _ = relay.call("DELETE", "", owner=True)
            print("The relay forgot this channel." if code == 200 else f"The relay answered {code}; the old key is gone here anyway.")
        except OSError as error:
            print(f"Could not reach the relay ({error}); the old key is gone here anyway.")
        relay.close()
    kept = {name: found[name] for name in ("relay", "page", "local") if found.get(name)}
    write_json(folder / "settings.json", kept)
    for name in ("feed.jsonl", "seen.json", "chats.json", "pair.png", "serve.json", "active"):
        (folder / name).unlink(missing_ok=True)
    for path in [*inbox(folder).glob("*"), *files(folder).glob("*"), *folder.glob("listening-*")]:
        path.unlink(missing_ok=True)
    print(f"Unlinked. `{ME} pair` makes a new code.")
    return 0


# ---------------------------------------------------------------- the right Python
NEEDS = {"cryptography": "cryptography", "segno": "segno", "PIL": "pillow"}  # module -> its pip name


def lacking() -> list[str]:
    """The packages this Python does not have."""
    from importlib.util import find_spec

    return [pip_name for module, pip_name in NEEDS.items() if find_spec(module) is None]


def python_with_packages(folder: Path) -> str:
    """A Python on this PC that has the packages, or "".

    A chat app may run its commands with a Python of its own that lacks them, while the Python the
    packages were installed for sits on the PATH. The one found is remembered for the next call.
    """
    kept = folder / "python.txt"
    candidates = [str(read_text(kept)).strip()]
    names = ("python.exe", "python3.exe") if os.name == "nt" else ("python3", "python")
    for place in os.environ.get("PATH", "").split(os.pathsep):
        candidates += [str(Path(place) / name) for name in names if place]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        installed = Path(os.environ["LOCALAPPDATA"]).glob("Programs/Python/Python3*/python.exe")
        candidates += sorted((str(path) for path in installed), reverse=True)
    tried: set[str] = set()
    for candidate in candidates:
        same = os.path.normcase(os.path.abspath(candidate)) if candidate else ""
        if not same or same in tried or not os.path.isfile(candidate):
            continue
        tried.add(same)
        try:
            probe = subprocess.run([candidate, "-c", "import " + ", ".join(NEEDS)], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            folder.mkdir(parents=True, exist_ok=True)
            kept.write_text(candidate, encoding="utf-8")
            return candidate
    return ""


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def main(arguments: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog=ME, description="See and steer the AI chats on this PC from your phone.")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair", help="make the QR code that links a phone")
    pair.add_argument("--relay", default="", help="address of your own relay, if you run one")
    pair.add_argument("--page", default="", help="address of the phone page, if it is not at the relay")
    pair.add_argument("--local", action="store_true", help="no relay: this PC serves the page, for a phone on the same Wi-Fi")
    pair.add_argument("--port", type=int, default=LOCAL_PORT, help=f"local mode: the port to serve on (default {LOCAL_PORT})")
    pair.add_argument("--host", default="", help="local mode: this PC's name or address as the phone reaches it (default <its name>.local)")
    pair.add_argument("--show-link", action="store_true", help="print the link (it holds the key)")
    pair.add_argument("--open", action="store_true", help="open the QR picture")
    pair.add_argument("--code", action="store_true", help="show 4 digits to type on the phone instead of scanning")
    pair.add_argument("--no-code", action="store_true", help="take back the code that waits for a phone")
    on = commands.add_parser("on", help="phone mode on for a chat; prints its chat id")
    on.add_argument("--label", default="", help="a name for this chat, shown on the phone")
    on.add_argument("--chat", default="", help="the id of a chat that was on before, to switch it on again")
    off = commands.add_parser("off", help="phone mode off")
    off.add_argument("--chat", default="", help="which chat, when several are on")
    off.add_argument("--all", action="store_true", help="every chat")
    say = commands.add_parser("say", help="post to the phone")
    say.add_argument("--kind", choices=KINDS, default="update")
    say.add_argument("--file", default="", help="read the text from this UTF-8 file ('-' for standard input)")
    say.add_argument("--image", default="", help="a picture to show on the phone (PNG, JPEG, ...)")
    say.add_argument("--chat", default="", help="which chat, when several are on")
    say.add_argument("words", nargs="*")
    listen = commands.add_parser("listen", help="wait for the next prompt from the phone")
    listen.add_argument("--wait", type=float, default=0, help="seconds to wait; 0 waits until one comes")
    listen.add_argument("--chat", default="", help="which chat, when several are on")
    step = commands.add_parser("step", help="say what a chat is doing, on the board of every chat")
    step.add_argument("--chat", default="", help="the chat's id; the first step of a new chat prints one")
    step.add_argument("--title", default="", help="what the chat is about: starts its row, or renames it")
    step.add_argument("--agent", default="", help="what runs the chat, e.g. vector")
    step.add_argument("--progress", type=int, default=None, help="how far it is, 0 to 100")
    step.add_argument("--end", choices=ENDINGS, default="", help="the chat is over, and how it ended")
    step.add_argument("words", nargs="*")
    board = commands.add_parser("board", help="every chat on this PC and its last step")
    board.add_argument("--notes", default="", help="also show the chats written down as session notes in this folder")
    board.add_argument("--machine", default="", help="with --notes: only the notes of this computer (default: all)")
    board.add_argument("--agent", default="", help="with --notes: only the notes of this agent (default: all)")
    board.add_argument("--no-notes", action="store_true", help="stop reading the folder of notes")
    commands.add_parser("view", help="open the page on this PC's own browser, on All chats")
    commands.add_parser("status", help="what is linked and running")  # listen: 0 prompt or none, 3 off, 4 replaced
    commands.add_parser("serve", help="run the helper in this window (it normally starts by itself)")
    commands.add_parser("pair-wait", help="wait for the phone that types the code (`pair --code` starts this by itself)")
    commands.add_parser("stop", help="stop the helper")
    commands.add_parser("forget", help="unlink every phone and clear the feed")
    chosen = parser.parse_args(arguments)
    folder = home()
    short = lacking()
    if short:  # hand the same command to a Python that has the packages (once: no going round in circles)
        other = "" if os.environ.get("VECTOR_RELAY_HANDED_OVER") else python_with_packages(folder)
        if other:
            again = [other, str(Path(__file__).resolve()), *(sys.argv[1:] if arguments is None else arguments)]
            return subprocess.call(again, env={**os.environ, "VECTOR_RELAY_HANDED_OVER": "1"})
        print(f"This Python lacks: {', '.join(short)}. Run:  \"{sys.executable}\" -m pip install {' '.join(short)}")
        return 2
    if chosen.command == "pair":
        return command_pair(folder, chosen.relay, chosen.page, chosen.show_link, chosen.open,
                            chosen.local, chosen.port, chosen.host, chosen.code, chosen.no_code)
    if chosen.command == "on":
        return command_on(folder, chosen.label, chosen.chat)
    if chosen.command == "off":
        return command_off(folder, chosen.chat, chosen.all)
    if chosen.command == "say":
        return command_say(folder, chosen.kind, chosen.words, chosen.file, chosen.image, chosen.chat)
    if chosen.command == "listen":
        return command_listen(folder, chosen.wait, chosen.chat)
    if chosen.command == "step":
        return command_step(folder, chosen.words, chosen.chat, chosen.title, chosen.agent, chosen.progress, chosen.end)
    if chosen.command == "board":
        return command_board(folder, chosen.notes, chosen.no_notes, chosen.machine, chosen.agent)
    if chosen.command == "view":
        return command_view(folder)
    if chosen.command == "status":
        return command_status(folder)
    if chosen.command == "serve":
        return serve(folder)
    if chosen.command == "pair-wait":
        return wait_for_code(folder)
    if chosen.command == "stop":
        return command_stop(folder)
    return command_forget(folder)


if __name__ == "__main__":
    sys.exit(main())
