"""The relay's rules (functions/c/[[path]].js) in memory, for tests on this PC.

Same paths, limits and answers as the Pages Function, so the helper and the phone page meet here what they
meet on Cloudflare. It keeps everything in one dictionary and forgets it when the test ends.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_STATE, MAX_ASK, MAX_ASKS, MAX_ANSWER = 300_000, 1_200_000, 30, 1_600_000
CHANNEL = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
ASK_ID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
TAKE_HOLD_S, ANSWER_HOLD_S, LOOK_S = 20, 10, 0.05


class FakeRelay(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.lock = threading.Lock()
        self.owners: dict[str, str] = {}
        self.states: dict[str, str] = {}
        self.asks: dict[str, dict[str, str]] = {}
        self.answers: dict[str, dict[str, str]] = {}
        self.calls = 0
        self.closing = False
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def handle_error(self, request, client_address) -> None:
        pass  # a helper that was stopped in the middle of a held request

    def close(self) -> None:
        self.closing = True
        self.shutdown()
        self.server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *arguments) -> None:
        pass

    def reply(self, status: int, body: str = "", kind: str = "text/plain") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Relay-Write")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if status != 204:
            self.wfile.write(data)

    def owner(self, channel: str, claim: bool) -> bool:
        token = self.headers.get("X-Relay-Write") or ""
        if len(token) < 20:
            return False
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        known = self.server.owners.get(channel)
        if known is None:
            if not claim:
                return False
            self.server.owners[channel] = digest
            return True
        return known == digest

    def held(self, most: float) -> float:
        """The moment until which this request may be held: what ?wait= asks for, up to `most` seconds."""
        asked = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("wait", ["0"])[0]
        try:
            return time.time() + min(most, max(0.0, float(asked)))
        except ValueError:
            return time.time()

    def handle_one(self, method: str) -> None:
        relay = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        parts = [part for part in self.path.split("?")[0].split("/") if part]
        if method == "OPTIONS":
            return self.reply(204)
        if len(parts) < 2 or parts[0] != "c" or not CHANNEL.match(parts[1]):
            return self.reply(404, "Not found.")
        channel, what = parts[1], "/".join(parts[2:])
        if what == "asks/take" and method == "POST":
            until = self.held(TAKE_HOLD_S)
            while time.time() < until and not relay.asks.get(channel) and not relay.closing:
                time.sleep(LOOK_S)
        if len(parts) == 4 and parts[2] == "answer" and method == "GET":
            until = self.held(ANSWER_HOLD_S)
            while time.time() < until and parts[3] not in relay.answers.get(channel, {}) and not relay.closing:
                time.sleep(LOOK_S)
        with relay.lock:
            relay.calls += 1
            if what == "state" and method == "PUT":
                if not body or len(body) > MAX_STATE:
                    return self.reply(413, "Too large.")
                if not self.owner(channel, True):
                    return self.reply(403, "Not yours.")
                relay.states[channel] = body
                return self.reply(200, "ok")
            if what == "state" and method == "GET":
                state = relay.states.get(channel)
                return self.reply(404, "Nothing here.") if state is None else self.reply(200, state)
            if what == "" and method == "DELETE":
                if not self.owner(channel, False):
                    return self.reply(404, "Nothing here.")
                for kept in (relay.states, relay.owners, relay.asks, relay.answers):
                    kept.pop(channel, None)
                return self.reply(200, "ok")
            ask_id = parts[3] if len(parts) == 4 else ""
            if len(parts) == 4 and parts[2] == "ask" and method == "POST":
                if not ASK_ID.match(ask_id):
                    return self.reply(404, "Not found.")
                if not body or len(body) > MAX_ASK:
                    return self.reply(413, "Too large.")
                if channel not in relay.owners:
                    return self.reply(404, "Nothing here.")
                waiting = relay.asks.setdefault(channel, {})
                if len(waiting) >= MAX_ASKS:
                    return self.reply(429, "The PC has not picked up the last questions yet.")
                waiting[ask_id] = body
                return self.reply(200, "ok")
            if what == "asks/take" and method == "POST":
                if not self.owner(channel, False):
                    return self.reply(404, "Nothing here.")
                waiting = relay.asks.pop(channel, {})
                asks = [{"id": key, "body": value} for key, value in waiting.items()]
                return self.reply(200, json.dumps({"asks": asks}), "application/json")
            if len(parts) == 4 and parts[2] == "answer" and ASK_ID.match(ask_id):
                if method == "GET":
                    answer = relay.answers.get(channel, {}).pop(ask_id, None)
                    return self.reply(204) if answer is None else self.reply(200, answer)
                if method == "PUT":
                    if not body or len(body) > MAX_ANSWER:
                        return self.reply(413, "Too large.")
                    if not self.owner(channel, False):
                        return self.reply(404, "Nothing here.")
                    relay.answers.setdefault(channel, {})[ask_id] = body
                    return self.reply(200, "ok")
        return self.reply(404, "Not found.")

    def do_GET(self) -> None:
        self.handle_one("GET")

    def do_POST(self) -> None:
        self.handle_one("POST")

    def do_PUT(self) -> None:
        self.handle_one("PUT")

    def do_DELETE(self) -> None:
        self.handle_one("DELETE")

    def do_OPTIONS(self) -> None:
        self.handle_one("OPTIONS")
