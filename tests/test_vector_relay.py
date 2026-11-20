"""Vector Relay on this PC: the feed, the inbox, the commands, and a whole trip through a relay.

Run from the repo:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import vector_relay as relay  # noqa: E402
from tests.fake_relay import FakeRelay  # noqa: E402

SCRATCH = REPO / ".tmp"
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run(folder: Path, *arguments: str, wait: float = 60) -> subprocess.CompletedProcess:
    """One `vector_relay.py` command, as the chat's agent runs it."""
    return subprocess.run(
        [sys.executable, str(REPO / "vector_relay.py"), *arguments], capture_output=True, timeout=wait,
        env={**os.environ, "VECTOR_RELAY_HOME": str(folder), "PYTHONIOENCODING": "utf-8"}, encoding="utf-8",
    )


class Phone:
    """What the phone page does, in Python: seal a question, leave it, look for the answer."""

    def __init__(self, found: dict):
        self.base = f"{found['relay']}/c/{found['channel']}"
        self.key = relay.unb64(found["key"])

    def http(self, method: str, path: str, body: str | None = None) -> tuple[int, str]:
        request = urllib.request.Request(self.base + path, method=method, data=None if body is None else body.encode("utf-8"))
        try:
            with DIRECT.open(request, timeout=10) as reply:
                return reply.status, reply.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            return error.code, ""

    def ask(self, route: str, wait: float = 12, **more) -> dict:
        ask_id = secrets.token_urlsafe(12)
        sealed = relay.seal(self.key, {"route": route, "t": int(time.time() * 1000), **more})
        status, _ = self.http("POST", "/ask/" + ask_id, sealed)
        assert status == 200, status
        deadline = time.time() + wait
        while time.time() < deadline:
            status, body = self.http("GET", "/answer/" + ask_id + "?wait=5")
            if status == 200:
                return relay.unseal(self.key, body)
        raise TimeoutError("the PC did not answer")


class Case(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix="home-", dir=SCRATCH))
        self.key = os.urandom(32)

    def chat_on(self, chat: str = "abcd", label: str = "") -> str:
        """A chat in phone mode, without the helper."""
        found = relay.read_json(self.folder / "chats.json", {})
        found[chat] = {"label": label, "on": True, "since": int(time.time())}
        relay.write_json(self.folder / "chats.json", found)
        return chat

    def tearDown(self) -> None:
        if relay.helper_running(self.folder):
            run(self.folder, "stop")
        shutil.rmtree(self.folder, ignore_errors=True)

    def sealed_prompt(self, text: str, prompt_id: str = "prompt-0001", age_s: float = 0, key: bytes | None = None, **more) -> str:
        ask = {"route": "prompt", "t": int((time.time() - age_s) * 1000), "pid": prompt_id, "text": text, **more}
        return relay.seal(key or self.key, ask)


class SealTests(Case):
    def test_round_trip_keeps_every_character(self) -> None:
        data = {"text": "公差分析 done, RSS 0.04 mm → ok", "n": 3}
        self.assertEqual(relay.unseal(self.key, relay.seal(self.key, data)), data)

    def test_another_key_opens_nothing(self) -> None:
        self.assertIsNone(relay.unseal(os.urandom(32), relay.seal(self.key, {"a": 1})))
        self.assertIsNone(relay.unseal(self.key, "not sealed at all"))


class FeedTests(Case):
    def test_events_are_numbered_in_order(self) -> None:
        first = relay.add_event(self.folder, "update", "one")
        second = relay.add_event(self.folder, "result", "two")
        self.assertEqual((first["n"], second["n"]), (1, 2))
        self.assertEqual([event["text"] for event in relay.read_feed(self.folder)], ["one", "two"])

    def test_four_writers_at_once_lose_nothing(self) -> None:
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); import vector_relay as relay; from pathlib import Path\n"
            "for i in range(25): relay.add_event(Path(sys.argv[2]), 'update', sys.argv[3] + str(i))\n"
        )
        writers = [subprocess.Popen([sys.executable, "-c", script, str(REPO), str(self.folder), f"w{w}-"]) for w in range(4)]
        self.assertEqual([writer.wait(timeout=90) for writer in writers], [0, 0, 0, 0])
        events = relay.read_feed(self.folder)
        self.assertEqual([event["n"] for event in events], list(range(1, 101)))
        self.assertEqual(len({event["text"] for event in events}), 100)

    def test_the_feed_file_is_cut_back_and_numbers_go_on(self) -> None:
        for number in range(relay.KEEP_EVENTS + 101):
            relay.add_event(self.folder, "update", str(number))
        events = relay.read_feed(self.folder)
        self.assertEqual(len(events), relay.KEEP_EVENTS + 1)
        self.assertEqual(events[-1]["n"], relay.KEEP_EVENTS + 101)

    def test_answer_holds_what_is_newer_and_cuts_long_text(self) -> None:
        relay.add_event(self.folder, "update", "old")
        relay.add_event(self.folder, "result", "x" * (relay.FEED_TEXT + 50))
        answer = relay.feed_answer(self.folder, after=1)
        self.assertEqual([event["n"] for event in answer["events"]], [2])
        self.assertEqual(len(answer["events"][0]["text"]), relay.FEED_TEXT)
        self.assertTrue(answer["events"][0]["cut"])
        self.assertEqual(answer["last"], 2)

    def test_a_phone_that_remembers_a_cleared_feed_gets_everything(self) -> None:
        relay.add_event(self.folder, "update", "new feed")
        self.assertEqual(len(relay.feed_answer(self.folder, after=99)["events"]), 1)

    def test_answer_stays_under_the_relay_limit(self) -> None:
        for number in range(relay.FEED_EVENTS):
            relay.add_event(self.folder, "result", "測" * relay.FEED_TEXT)
        sealed = relay.seal(self.key, relay.feed_answer(self.folder))
        self.assertLess(len(sealed), 2_000_000)
        state = relay.feed_answer(self.folder, limit=relay.STATE_EVENTS, text_max=relay.STATE_TEXT, bytes_max=relay.STATE_BYTES)
        self.assertLess(len(relay.seal(self.key, state)), 300_000)


class PromptTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.chat = self.chat_on()

    def answer(self, body: str, seen: list | None = None) -> dict:
        return relay.answer_ask(self.folder, self.key, [] if seen is None else seen, body)

    def test_a_prompt_lands_in_the_inbox_and_the_feed(self) -> None:
        answer = self.answer(self.sealed_prompt("check the yield deck"))
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["waiting"], ["prompt-0001"])
        self.assertEqual(relay.read_feed(self.folder)[0]["from"], "phone")
        self.assertEqual(relay.read_feed(self.folder)[0]["chat"], self.chat)
        self.assertEqual(relay.take_prompt(self.folder, self.chat)["text"], "check the yield deck")
        self.assertIsNone(relay.take_prompt(self.folder, self.chat))

    def test_the_same_prompt_twice_is_carried_out_once(self) -> None:
        seen: list[str] = []
        self.answer(self.sealed_prompt("send it"), seen)
        again = self.answer(self.sealed_prompt("send it"), seen)
        self.assertTrue(again["ok"])
        self.assertEqual(len(relay.waiting_ids(self.folder)), 1)
        self.assertEqual(len(relay.read_feed(self.folder)), 1)

    def test_seen_prompts_survive_a_restart_of_the_helper(self) -> None:
        self.answer(self.sealed_prompt("send it"), [])
        relay.take_prompt(self.folder, self.chat)
        seen = relay.read_json(self.folder / "seen.json", [])
        self.answer(self.sealed_prompt("send it"), seen)
        self.assertEqual(relay.waiting_ids(self.folder), [])

    def test_an_old_recording_is_refused(self) -> None:
        answer = self.answer(self.sealed_prompt("delete everything", age_s=relay.PROMPT_MAX_AGE_S + 5))
        self.assertEqual(answer, {"ok": False, "error": "old"})
        self.assertEqual(relay.waiting_ids(self.folder), [])

    def test_a_prompt_sealed_with_another_key_is_refused(self) -> None:
        answer = self.answer(self.sealed_prompt("do something", key=os.urandom(32)))
        self.assertEqual(answer, {"ok": False, "error": "key"})
        self.assertEqual(relay.waiting_ids(self.folder), [])

    def test_empty_long_or_odd_prompts_are_refused(self) -> None:
        for text, prompt_id in (("  ", "prompt-0001"), ("x" * (relay.MAX_PROMPT + 1), "prompt-0001"), ("fine", "../../x")):
            self.assertEqual(self.answer(self.sealed_prompt(text, prompt_id)), {"ok": False, "error": "bad"})
        self.assertEqual(relay.waiting_ids(self.folder), [])

    def test_prompts_come_out_oldest_first(self) -> None:
        for number in range(3):
            relay.put_prompt(self.folder, self.chat, f"prompt-000{number}", f"task {number}")
        self.assertEqual([relay.take_prompt(self.folder, self.chat)["text"] for _ in range(3)], ["task 0", "task 1", "task 2"])

    def test_the_whole_text_of_a_cut_event_can_be_asked_for(self) -> None:
        relay.add_event(self.folder, "result", "y" * (relay.FEED_TEXT + 500))
        answer = self.answer(relay.seal(self.key, {"route": "event", "n": 1, "t": 0}))
        self.assertEqual(len(answer["event"]["text"]), relay.FEED_TEXT + 500)


class ChatTests(Case):
    """Several chats in phone mode at once, and pictures both ways."""

    def answer(self, body: str, seen: list | None = None) -> dict:
        return relay.answer_ask(self.folder, self.key, [] if seen is None else seen, body)

    def test_a_prompt_goes_to_the_chat_the_phone_named_and_to_no_other(self) -> None:
        first, second = self.chat_on("aaaa", "Atlas FA"), self.chat_on("bbbb", "Weekly")
        self.assertTrue(self.answer(self.sealed_prompt("for the weekly", chat=second))["ok"])
        self.assertIsNone(relay.take_prompt(self.folder, first))
        self.assertEqual(relay.take_prompt(self.folder, second)["text"], "for the weekly")

    def test_with_several_chats_a_prompt_must_name_one(self) -> None:
        self.chat_on("aaaa")
        self.chat_on("bbbb")
        self.assertEqual(self.answer(self.sealed_prompt("which one?")), {"ok": False, "error": "chat"})
        self.assertEqual(relay.waiting_ids(self.folder), [])

    def test_a_prompt_for_a_chat_that_is_off_or_unknown_is_refused(self) -> None:
        self.assertEqual(self.answer(self.sealed_prompt("anyone?")), {"ok": False, "error": "off"})
        chat = self.chat_on("aaaa")
        found = relay.read_json(self.folder / "chats.json", {})
        found[chat]["on"] = False
        relay.write_json(self.folder / "chats.json", found)
        self.assertEqual(self.answer(self.sealed_prompt("hello", chat=chat)), {"ok": False, "error": "off"})
        self.assertEqual(self.answer(self.sealed_prompt("hello", "prompt-0002", chat="zzzz")), {"ok": False, "error": "off"})
        self.assertEqual(relay.waiting_ids(self.folder), [])
        self.assertEqual(relay.read_feed(self.folder), [])

    def test_the_feed_answer_lists_the_chats(self) -> None:
        self.chat_on("aaaa", "Atlas FA")
        self.chat_on("bbbb", "Weekly")
        answer = relay.feed_answer(self.folder)
        self.assertEqual([(chat["id"], chat["label"], chat["on"]) for chat in answer["chats"]], [("aaaa", "Atlas FA", True), ("bbbb", "Weekly", True)])
        self.assertTrue(answer["mode"]["on"])

    def test_a_picture_is_made_small_and_the_phone_can_ask_for_it(self) -> None:
        from PIL import Image

        source = self.folder / "plot.png"
        Image.effect_noise((2600, 1800), 80).convert("RGB").save(source)
        self.chat_on()
        said = run(self.folder, "say", "--kind", "result", "--image", str(source), "the leak plot")
        self.assertIn("ok n=1", said.stdout, said.stdout + said.stderr)
        event = relay.read_feed(self.folder)[0]
        self.assertLessEqual(max(event["img"]["w"], event["img"]["h"]), relay.PICTURE_SIDE)
        self.assertLessEqual((self.folder / "files" / event["img"]["f"]).stat().st_size, relay.PICTURE_BYTES)
        answer = self.answer(relay.seal(self.key, {"route": "image", "n": 1, "t": 0}))
        self.assertTrue(answer["ok"])
        self.assertEqual(relay.base64.b64decode(answer["data"])[:3], b"\xff\xd8\xff")
        self.assertLess(len(relay.seal(self.key, answer)), 1_600_000)
        self.assertEqual(self.answer(relay.seal(self.key, {"route": "image", "n": 7, "t": 0})), {"ok": False, "error": "gone"})

    def test_a_picture_that_cannot_be_read_says_so(self) -> None:
        broken = self.folder / "broken.png"
        broken.write_text("not a picture", encoding="utf-8")
        said = run(self.folder, "say", "--image", str(broken), "look")
        self.assertEqual(said.returncode, 2)
        self.assertIn("Could not read the picture", said.stdout)
        self.assertEqual(relay.read_feed(self.folder), [])

    def photo(self) -> str:
        import io

        from PIL import Image

        packed = io.BytesIO()
        Image.new("RGB", (320, 240), (200, 80, 40)).save(packed, "JPEG")
        return relay.base64.b64encode(packed.getvalue()).decode("ascii")

    def test_a_photo_from_the_phone_is_kept_as_a_file_for_the_chat(self) -> None:
        chat = self.chat_on()
        answer = self.answer(self.sealed_prompt("", chat=chat, img=self.photo(), w=320, h=240))
        self.assertTrue(answer["ok"])
        prompt = relay.take_prompt(self.folder, chat)
        self.assertEqual(prompt["text"], "(photo)")
        self.assertEqual(Path(prompt["image"]).read_bytes()[:3], b"\xff\xd8\xff")
        self.assertEqual(relay.read_feed(self.folder)[0]["img"], {"f": "in-prompt-0001.jpg", "w": 320, "h": 240})

    def test_a_photo_gets_longer_to_arrive_than_text_but_not_for_ever(self) -> None:
        chat = self.chat_on()
        late = relay.PROMPT_MAX_AGE_S + 10
        self.assertTrue(self.answer(self.sealed_prompt("see this", chat=chat, age_s=late, img=self.photo()))["ok"])
        self.assertEqual(self.answer(self.sealed_prompt("see this", "prompt-0002", chat=chat, age_s=late)), {"ok": False, "error": "old"})
        too_late = relay.PHOTO_MAX_AGE_S + 5
        self.assertEqual(self.answer(self.sealed_prompt("x", "prompt-0003", chat=chat, age_s=too_late, img=self.photo())), {"ok": False, "error": "old"})

    def test_what_is_not_a_jpeg_is_not_kept(self) -> None:
        chat = self.chat_on()
        for number, data in enumerate(("bm90IGEgcGhvdG8=", "***", "A" * (relay.PHOTO_CHARS + 4))):
            answer = self.answer(self.sealed_prompt("see this", f"prompt-000{number}", chat=chat, img=data))
            self.assertEqual(answer, {"ok": False, "error": "photo"})
        self.assertEqual(relay.waiting_ids(self.folder), [])
        self.assertEqual(list((self.folder / "files").glob("*")), [])


class CommandTests(Case):
    def test_before_a_phone_is_linked_the_commands_say_what_to_do(self) -> None:
        on = run(self.folder, "on")
        self.assertEqual(on.returncode, 2)
        self.assertIn("vector_relay.py pair --open", on.stdout)
        self.assertEqual(run(self.folder, "listen", "--wait", "1").returncode, 3)

    def test_pair_needs_no_address_and_the_page_is_at_the_relay(self) -> None:
        self.assertEqual(run(self.folder, "pair").returncode, 0)
        found = relay.settings(self.folder)
        self.assertEqual((found["relay"], found["page"]), (relay.RELAY, relay.RELAY + "/"))

    def test_only_an_https_relay_is_taken(self) -> None:
        self.assertEqual(run(self.folder, "pair", "--relay", "http://example.com").returncode, 2)
        self.assertEqual(relay.settings(self.folder), {})

    def test_pair_makes_a_code_the_phone_can_read_and_keeps_the_key_out_of_the_output(self) -> None:
        made = run(self.folder, "pair", "--relay", "https://relay.example.pages.dev/")
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        found = relay.settings(self.folder)
        self.assertEqual(found["relay"], "https://relay.example.pages.dev")
        self.assertNotIn(found["key"], made.stdout)
        self.assertNotIn("#r=", made.stdout)
        self.assertTrue((self.folder / "pair.png").stat().st_size > 500)
        read = json.loads(relay.unb64(relay.ticket(found)))
        self.assertEqual(read, {"u": found["relay"], "c": found["channel"], "k": found["key"]})
        again = run(self.folder, "pair", "--show-link")
        self.assertIn(found["page"] + "#r=" + relay.ticket(found), again.stdout)
        self.assertEqual(relay.settings(self.folder)["key"], found["key"])

    def test_say_reads_a_file_and_refuses_nothing(self) -> None:
        note = self.folder / "result.md"
        note.write_text("## 結果\n\n| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")
        said = run(self.folder, "say", "--kind", "result", "--file", str(note))
        self.assertIn("ok n=1", said.stdout)
        self.assertTrue(relay.read_feed(self.folder)[0]["text"].startswith("## 結果"))
        self.assertEqual(run(self.folder, "say", "  ").returncode, 2)


class TripTests(Case):
    """Phone -> relay -> helper -> chat, and back, with the relay's own rules."""

    def setUp(self) -> None:
        super().setUp()
        self.fake = FakeRelay()
        self.assertEqual(run(self.folder, "pair", "--relay", self.fake.address).returncode, 0)
        self.phone = Phone(relay.settings(self.folder))

    def tearDown(self) -> None:
        super().tearDown()
        self.fake.close()

    def test_prompt_from_the_phone_and_result_back(self) -> None:
        on = run(self.folder, "on", "--label", "Atlas FA")
        self.assertEqual(on.returncode, 0, on.stdout + on.stderr)

        feed = self.phone.ask("feed", after=0)
        self.assertTrue(feed["ok"])
        chat = on.stdout.split("chat=")[1][:4]
        self.assertEqual(feed["mode"]["label"], "Atlas FA")
        self.assertEqual([(found["id"], found["label"], found["on"]) for found in feed["chats"]], [(chat, "Atlas FA", True)])
        self.assertEqual(feed["events"][0]["text"], "Phone mode on: Atlas FA")
        self.assertFalse(feed["listening"])

        listener = subprocess.Popen(
            [sys.executable, str(REPO / "vector_relay.py"), "listen", "--wait", "40"], stdout=subprocess.PIPE, encoding="utf-8",
            env={**os.environ, "VECTOR_RELAY_HOME": str(self.folder)},
        )
        deadline = time.time() + 10
        while time.time() < deadline and not relay.listening(self.folder):
            time.sleep(0.1)
        self.assertTrue(self.phone.ask("feed", after=0)["listening"])

        text = "更新 Atlas 的 O-ring 結果\nthen tell me the margin"
        sent = self.phone.ask("prompt", pid="phone-prompt-01", text=text, chat=chat, after=feed["last"])
        self.assertTrue(sent["ok"])
        heard, _ = listener.communicate(timeout=20)
        lines = heard.splitlines()
        self.assertEqual(lines[0], "LISTENING")
        self.assertTrue(lines[1].startswith("PROMPT id=phone-prompt-01 at="))
        self.assertTrue(lines[1].endswith(f"chat={chat}"))
        self.assertEqual("\n".join(lines[2:]), text)

        self.assertIn("ok n=", run(self.folder, "say", "reading the leak data").stdout)
        self.assertIn("ok n=", run(self.folder, "say", "--kind", "result", "Margin **0.04 mm**").stdout)
        feed = self.phone.ask("feed", after=feed["last"])
        self.assertEqual([event["kind"] for event in feed["events"]], ["prompt", "update", "result"])
        self.assertEqual(feed["waiting"], [])
        self.assertFalse(feed["listening"])

        self.assertEqual(run(self.folder, "off").returncode, 0)
        feed = self.phone.ask("feed", after=feed["last"])
        self.assertFalse(feed["mode"]["on"])
        self.assertEqual(feed["events"][-1]["text"], "Phone mode off")

    def test_two_chats_each_hear_only_their_own_prompt(self) -> None:
        chats = [run(self.folder, "on", "--label", label).stdout.split("chat=")[1][:4] for label in ("Atlas FA", "Weekly")]
        self.assertEqual(len(set(chats)), 2)
        several = run(self.folder, "say", "which chat am I?")
        self.assertEqual(several.returncode, 2)
        self.assertIn("--chat", several.stdout)
        listeners = [
            subprocess.Popen(
                [sys.executable, str(REPO / "vector_relay.py"), "listen", "--wait", "40", "--chat", chat], stdout=subprocess.PIPE,
                encoding="utf-8", env={**os.environ, "VECTOR_RELAY_HOME": str(self.folder)},
            ) for chat in chats
        ]
        deadline = time.time() + 10
        while time.time() < deadline and not all(relay.listening(self.folder, chat) for chat in chats):
            time.sleep(0.1)
        feed = self.phone.ask("feed", after=0)
        self.assertEqual([found["listening"] for found in feed["chats"]], [True, True])
        self.assertTrue(self.phone.ask("prompt", pid="for-the-second-1", text="weekly first", chat=chats[1])["ok"])
        heard, _ = listeners[1].communicate(timeout=20)
        self.assertIn(f"chat={chats[1]}\nweekly first", heard)
        self.assertIsNone(listeners[0].poll())
        self.assertIn("ok n=", run(self.folder, "say", "--chat", chats[1], "--kind", "result", "weekly is done").stdout)
        self.assertEqual(relay.read_feed(self.folder)[-1]["chat"], chats[1])
        self.assertTrue(self.phone.ask("prompt", pid="for-the-first-01", text="atlas now", chat=chats[0])["ok"])
        heard, _ = listeners[0].communicate(timeout=20)
        self.assertIn(f"chat={chats[0]}\natlas now", heard)
        left = run(self.folder, "off", "--chat", chats[0])
        self.assertIn("1 other chat(s) still on", left.stdout)
        feed = self.phone.ask("feed", after=0)
        self.assertEqual([found["on"] for found in feed["chats"]], [False, True])
        self.assertEqual(self.phone.ask("prompt", pid="for-the-first-02", text="still there?", chat=chats[0]), {"ok": False, "error": "off"})

    def test_the_helper_hears_a_question_within_a_second_while_idle(self) -> None:
        run(self.folder, "on")
        self.phone.ask("feed", after=0)
        time.sleep(3)  # the helper now sits in a request the relay holds
        started = time.time()
        self.assertTrue(self.phone.ask("feed", after=0)["ok"])
        self.assertLess(time.time() - started, 1.5)
        calls = self.fake.calls
        time.sleep(4)
        self.assertLessEqual(self.fake.calls - calls, 2)  # and it does not ask over and over while nothing happens

    def test_the_relay_keeps_a_sealed_copy_for_a_pc_that_is_off(self) -> None:
        run(self.folder, "on")
        status, body = 0, ""
        deadline = time.time() + 10
        while time.time() < deadline and status != 200:
            status, body = self.phone.http("GET", "/state")
            time.sleep(0.2)
        self.assertEqual(status, 200)
        self.assertNotIn("Phone mode on", body)
        self.assertEqual(relay.unseal(self.phone.key, body)["events"][0]["text"], "Phone mode on")

    def test_one_helper_at_a_time_and_stop_ends_it(self) -> None:
        run(self.folder, "on")
        self.assertTrue(relay.helper_running(self.folder))
        second = run(self.folder, "serve", wait=15)
        self.assertEqual(second.returncode, 0)
        self.assertIn("stopped", run(self.folder, "stop").stdout)
        self.assertFalse(relay.helper_running(self.folder))

    def test_listen_ends_when_phone_mode_goes_off(self) -> None:
        run(self.folder, "on")
        listener = subprocess.Popen(
            [sys.executable, str(REPO / "vector_relay.py"), "listen"], stdout=subprocess.PIPE, encoding="utf-8",
            env={**os.environ, "VECTOR_RELAY_HOME": str(self.folder)},
        )
        time.sleep(1)
        run(self.folder, "off")
        heard, _ = listener.communicate(timeout=15)
        self.assertEqual(heard.split(), ["LISTENING", "OFF"])
        self.assertEqual(listener.returncode, 3)

    def test_a_listener_left_behind_gives_way_to_a_new_one(self) -> None:
        chat = run(self.folder, "on").stdout.split("chat=")[1][:4]
        command = [sys.executable, str(REPO / "vector_relay.py"), "listen", "--wait", "40", "--chat", chat]
        options = {"stdout": subprocess.PIPE, "encoding": "utf-8", "env": {**os.environ, "VECTOR_RELAY_HOME": str(self.folder)}}
        old = subprocess.Popen(command, **options)
        deadline = time.time() + 10
        while time.time() < deadline and not relay.listening(self.folder, chat):
            time.sleep(0.1)
        new = subprocess.Popen(command, **options)
        heard, _ = old.communicate(timeout=20)
        self.assertEqual((heard.split(), old.returncode), (["LISTENING", "REPLACED"], 4))
        self.assertTrue(self.phone.ask("prompt", pid="after-takeover-1", text="who hears this?", chat=chat)["ok"])
        heard, _ = new.communicate(timeout=20)
        self.assertIn("who hears this?", heard)

    def test_forget_wipes_the_channel_and_the_key(self) -> None:
        run(self.folder, "on")
        self.phone.ask("feed", after=0)
        forgot = run(self.folder, "forget")
        self.assertIn("The relay forgot this channel.", forgot.stdout)
        self.assertEqual(self.phone.http("GET", "/state")[0], 404)
        self.assertEqual(relay.settings(self.folder), {"relay": self.fake.address, "page": self.fake.address + "/"})
        self.assertEqual(relay.read_feed(self.folder), [])


def free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LocalTests(Case):
    """Local mode: no relay. This PC serves the page and answers the phone itself."""

    def setUp(self) -> None:
        super().setUp()
        self.port = free_port()

    def pair(self) -> Phone:
        made = run(self.folder, "pair", "--local", "--host", "127.0.0.1", "--port", str(self.port))
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        return Phone(relay.settings(self.folder))

    def page(self, path: str) -> tuple[int, str, str]:
        try:
            with DIRECT.open(f"http://127.0.0.1:{self.port}{path}", timeout=10) as reply:
                return reply.status, reply.headers.get("Content-Type", ""), reply.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, "", ""

    def test_pair_local_names_this_pc_and_its_port(self) -> None:
        made = run(self.folder, "pair", "--local", "--port", str(self.port))
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        found = relay.settings(self.folder)
        address = f"http://{relay.local_name()}:{self.port}"
        self.assertTrue(relay.local_name().endswith(".local"))
        self.assertEqual((found["relay"], found["page"], found["local"]), (address, address + "/", self.port))
        self.assertIn("same Wi-Fi", made.stdout)
        self.assertNotIn(found["key"], made.stdout)

    def test_local_mode_takes_only_this_pc_and_its_own_network(self) -> None:
        for host in ("example.com", "8.8.8.8", "172.32.0.1"):
            self.assertEqual(run(self.folder, "pair", "--local", "--host", host).returncode, 2, host)
        self.assertEqual(relay.settings(self.folder), {})
        for host in ("192.168.1.10", "10.0.0.7", "172.16.4.2", "my-pc.local"):
            self.assertEqual(run(self.folder, "pair", "--local", "--host", host, "--port", str(self.port)).returncode, 0, host)

    def test_going_local_makes_a_new_key(self) -> None:
        run(self.folder, "pair", "--relay", "https://relay.example.pages.dev")
        through_a_relay = relay.settings(self.folder)["key"]
        self.pair()
        local = relay.settings(self.folder)["key"]
        self.assertNotEqual(through_a_relay, local)
        self.pair()
        self.assertEqual(relay.settings(self.folder)["key"], local)

    def test_prompt_from_the_phone_and_result_back_with_no_relay(self) -> None:
        phone = self.pair()
        chat = run(self.folder, "on", "--label", "Local").stdout.split("chat=")[1][:4]
        self.assertTrue(relay.helper_running(self.folder))
        started = time.time()
        feed = phone.ask("feed", after=0)
        self.assertLess(time.time() - started, 2)
        self.assertEqual([(entry["id"], entry["label"], entry["on"]) for entry in feed["chats"]], [(chat, "Local", True)])
        listener = subprocess.Popen(
            [sys.executable, str(REPO / "vector_relay.py"), "listen", "--wait", "30", "--chat", chat], stdout=subprocess.PIPE,
            encoding="utf-8", env={**os.environ, "VECTOR_RELAY_HOME": str(self.folder)},
        )
        deadline = time.time() + 10
        while time.time() < deadline and not relay.listening(self.folder, chat):
            time.sleep(0.1)
        self.assertTrue(phone.ask("prompt", pid="local-prompt-01", text="再跑一次\nplease", chat=chat)["ok"])
        heard, _ = listener.communicate(timeout=20)
        self.assertEqual(heard.split("\n", 2)[2].rstrip("\n"), "再跑一次\nplease")
        run(self.folder, "say", "--chat", chat, "--kind", "result", "Done: 0.04 mm")
        texts = [event["text"] for event in phone.ask("feed", after=0)["events"]]
        self.assertEqual(texts[-2:], ["再跑一次\nplease", "Done: 0.04 mm"])
        self.assertIn("Mode:       local", run(self.folder, "status").stdout)

    def test_this_pc_serves_the_page_and_nothing_else(self) -> None:
        phone = self.pair()
        run(self.folder, "on")
        status, kind, body = self.page("/")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        self.assertIn("softcrypto.js", body)
        for name in ("app.js", "softcrypto.js", "styles.css", "icon.svg"):
            self.assertEqual(self.page("/" + name + "?v=1")[0], 200, name)
        for path in ("/vector_relay.py", "/settings.json", "/..%2fsettings.json", "/docs/app.js", "/c/", "/c/wrong-channel-000000/state"):
            self.assertEqual(self.page(path)[0], 404, path)
        found = relay.settings(self.folder)
        for secret in (found["key"], found["write"]):
            self.assertNotIn(secret, body)
        status, body = phone.http("GET", "/state")
        self.assertEqual(status, 200)
        self.assertNotIn("Phone mode on", body)
        self.assertEqual(relay.unseal(phone.key, body)["events"][0]["text"], "Phone mode on")

    def test_a_question_sealed_with_another_key_gets_no_feed(self) -> None:
        phone = self.pair()
        run(self.folder, "on")
        phone.key = os.urandom(32)
        ask_id = secrets.token_urlsafe(12)
        self.assertEqual(phone.http("POST", "/ask/" + ask_id, relay.seal(phone.key, {"route": "feed", "t": 0}))[0], 200)
        status, body = phone.http("GET", "/answer/" + ask_id)
        self.assertEqual(status, 200)
        self.assertIsNone(relay.unseal(phone.key, body))
        answer = relay.unseal(relay.unb64(relay.settings(self.folder)["key"]), body)
        self.assertEqual(answer, {"ok": False, "error": "key"})

    def test_the_helper_starts_again_right_after_it_stopped(self) -> None:
        self.pair()
        run(self.folder, "on")
        self.assertIn("stopped", run(self.folder, "stop").stdout)
        again = run(self.folder, "on")
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertTrue(relay.helper_running(self.folder))

    def test_forget_unlinks_the_phone_without_a_relay(self) -> None:
        phone = self.pair()
        run(self.folder, "on")
        phone.ask("feed", after=0)
        forgot = run(self.folder, "forget")
        self.assertIn("no longer linked", forgot.stdout)
        self.assertFalse(relay.helper_running(self.folder))
        found = relay.settings(self.folder)
        self.assertEqual(sorted(found), ["local", "page", "relay"])
        self.assertEqual(relay.read_feed(self.folder), [])


if __name__ == "__main__":
    unittest.main()
