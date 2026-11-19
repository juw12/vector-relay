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

Everything that leaves this PC is sealed with AES-256-GCM. The key is made here and reaches the
phone only inside the QR code, after the "#" of the address, which a browser sends to no server.
The relay (functions/ in this repo, on Cloudflare Pages with a D1 database) holds ciphertext it
cannot read, and can at worst lose or delay it. The phone needs the internet, not your Wi-Fi.

A prompt is carried out once: its id is remembered, and one older than PROMPT_MAX_AGE_S is refused,
so a recorded prompt cannot be played again and one the phone marked "not sent" does not arrive later.

Data lives in %LOCALAPPDATA%\\VectorRelay (or the folder VECTOR_RELAY_HOME names).
"""

from __future__ import annotations

import argparse
import base64
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
from pathlib import Path

VERSION = "1.0.0"
RELAY = "https://vector-relay.pages.dev"  # the page and the relay live at one address
KINDS = ("update", "result", "error", "note", "prompt")
PROMPT_ID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
CHAT_ID = re.compile(r"^[a-z0-9]{4}$")
RELAY_ADDRESS = re.compile(r"^(https://[^/\s]+|http://(127\.0\.0\.1|localhost)(:\d+)?)$")

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
        "chats": chat_list(folder), "waiting": waiting_ids(folder), "last": last, "events": chosen,
    }


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


def helper_running(folder: Path) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", guard_port(folder)), timeout=0.5):
            return True
    except OSError:
        return False


def start_helper(folder: Path) -> bool:
    """Starts the helper unless one runs. True when one is running afterwards."""
    if not paired(settings(folder)):
        return False
    if helper_running(folder):
        return True
    command = [sys.executable, str(Path(__file__).resolve()), "serve"]
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
               "env": {**os.environ, "VECTOR_RELAY_HOME": str(folder)}, "cwd": str(folder)}
    if os.name == "nt":
        # no console, its own group, normal priority (a job started from a chat runs below normal),
        # and outside the chat's job so it is still there when the chat's command has ended
        flags = 0x00000008 | 0x00000200 | 0x00000020
        try:
            subprocess.Popen(command, creationflags=flags | 0x01000000, **options)
        except OSError:
            subprocess.Popen(command, creationflags=flags, **options)
    else:
        subprocess.Popen(command, start_new_session=True, **options)
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


def serve(folder: Path) -> int:
    """Passes questions and answers between the relay and this folder until nobody needs it."""
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
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
    relay = Relay(found)
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
            if idle > (IDLE_ON_S if mode(folder)["on"] else IDLE_OFF_S) and not listening(folder):
                log(folder, "nobody needs the helper any more")
                break
            try:
                state = feed_answer(folder, limit=STATE_EVENTS, text_max=STATE_TEXT, bytes_max=STATE_BYTES)
                shown = (state["last"], json.dumps(state["chats"], sort_keys=True), len(state["waiting"]))
                due = shown != published and now - published_at >= PUBLISH_GAP_S
                if published is None or due or now - published_at >= HEARTBEAT_S:
                    code, _ = relay.call("PUT", "/state", seal(relay.key, state), owner=True)
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
        stop.unlink(missing_ok=True)
        guard.close()
        log(folder, "helper stopped")
    return 0


# ---------------------------------------------------------------- commands
ME = "vector_relay.py"
SEVERAL = "Several chats are in phone mode. Say which one with --chat <id>:"


def say_chats(folder: Path) -> None:
    for chat, entry in chats(folder).items():
        if entry["on"]:
            print(f"  {chat}  {entry['label'] or '(no name)'}")


def command_pair(folder: Path, relay: str, page: str, show_link: bool, open_picture: bool) -> int:
    found = settings(folder)
    if relay:
        relay = relay.rstrip("/")
        if not RELAY_ADDRESS.match(relay):
            print("The relay address must look like https://<name>.pages.dev")
            return 2
        if relay != found.get("relay"):
            found.pop("page", None)
        found["relay"] = relay
    found.setdefault("relay", RELAY)
    if page:
        found["page"] = page
    found.setdefault("page", found["relay"] + "/")
    if not paired(found):
        found.update(channel=secrets.token_urlsafe(16), key=b64(os.urandom(32)), write=secrets.token_urlsafe(24))
    write_json(folder / "settings.json", found)
    link = found["page"] + "#r=" + ticket(found)
    picture = folder / "pair.png"
    try:
        import segno

        segno.make(link, error="m").save(str(picture), scale=8, border=3)
        print(f"QR code: {picture}")
        print("Scan it with the phone's camera. It holds the key: do not share the picture.")
        if open_picture and hasattr(os, "startfile"):
            os.startfile(picture)  # noqa: S606 - the user's own picture viewer
    except ImportError:
        print("No QR picture (pip install segno). Use --show-link and open the link on the phone.")
    if show_link:
        print(link)
    return 0


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


def command_status(folder: Path) -> int:
    found, helper = settings(folder), read_json(folder / "serve.json", {})
    running = helper_running(folder)
    events = read_feed(folder)
    print(f"Vector Relay {VERSION}   data: {folder}")
    if not paired(found):
        print(f"Not linked to a phone yet. Run:  {ME} pair --open")
        return 0
    print(f"Relay:      {urllib.parse.urlsplit(found['relay']).netloc}")
    print(f"Phone page: {found.get('page', found['relay'] + '/')}")
    on = chats_on(folder)
    print(f"Phone mode: {'on in ' + str(len(on)) + ' chat(s)' if on else 'off'}")
    for chat, entry in chats(folder).items():
        if entry["on"]:
            state = "waiting for a prompt" if listening(folder, chat) else "working (not listening right now)"
            print(f"  {chat}  {entry['label'] or '(no name)'}: {state}, {len(waiting_ids(folder, chat))} prompt(s) waiting")
    print(f"Helper:     {'running' if running else 'not running'}")
    if running and helper.get("contact"):
        print(f"Relay last reached: {time.strftime('%H:%M:%S', time.localtime(helper['contact']))}")
    if running and helper.get("error"):
        print(f"Trouble:    {helper['error']}")
    print(f"Feed:       {len(events)} events")
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
    if paired(found):
        relay = Relay(found)
        try:
            code, _ = relay.call("DELETE", "", owner=True)
            print("The relay forgot this channel." if code == 200 else f"The relay answered {code}; the old key is gone here anyway.")
        except OSError as error:
            print(f"Could not reach the relay ({error}); the old key is gone here anyway.")
        relay.close()
    kept = {name: found[name] for name in ("relay", "page") if found.get(name)}
    write_json(folder / "settings.json", kept)
    for name in ("feed.jsonl", "seen.json", "chats.json", "pair.png", "serve.json", "active"):
        (folder / name).unlink(missing_ok=True)
    for path in [*inbox(folder).glob("*"), *files(folder).glob("*"), *folder.glob("listening-*")]:
        path.unlink(missing_ok=True)
    print(f"Unlinked. `{ME} pair` makes a new code.")
    return 0


def main(arguments: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog=ME, description="See and steer the AI chats on this PC from your phone.")
    commands = parser.add_subparsers(dest="command", required=True)
    pair = commands.add_parser("pair", help="make the QR code that links a phone")
    pair.add_argument("--relay", default="", help="address of your own relay, if you run one")
    pair.add_argument("--page", default="", help="address of the phone page, if it is not at the relay")
    pair.add_argument("--show-link", action="store_true", help="print the link (it holds the key)")
    pair.add_argument("--open", action="store_true", help="open the QR picture")
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
    commands.add_parser("status", help="what is linked and running")  # listen: 0 prompt or none, 3 off, 4 replaced
    commands.add_parser("serve", help="run the helper in this window (it normally starts by itself)")
    commands.add_parser("stop", help="stop the helper")
    commands.add_parser("forget", help="unlink every phone and clear the feed")
    chosen = parser.parse_args(arguments)
    folder = home()
    if chosen.command == "pair":
        return command_pair(folder, chosen.relay, chosen.page, chosen.show_link, chosen.open)
    if chosen.command == "on":
        return command_on(folder, chosen.label, chosen.chat)
    if chosen.command == "off":
        return command_off(folder, chosen.chat, chosen.all)
    if chosen.command == "say":
        return command_say(folder, chosen.kind, chosen.words, chosen.file, chosen.image, chosen.chat)
    if chosen.command == "listen":
        return command_listen(folder, chosen.wait, chosen.chat)
    if chosen.command == "status":
        return command_status(folder)
    if chosen.command == "serve":
        return serve(folder)
    if chosen.command == "stop":
        return command_stop(folder)
    return command_forget(folder)


if __name__ == "__main__":
    sys.exit(main())
