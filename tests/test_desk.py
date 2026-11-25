"""The phone desk: which chats it holds, and a prompt carried out by a stand-in agent.

Run from the repo:  python -m unittest tests.test_desk -v
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
import threading
import types
import time
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import desk  # noqa: E402
import vector_relay as relay  # noqa: E402

SCRATCH = REPO / ".tmp"


class DeskCase(unittest.TestCase):
    def setUp(self) -> None:
        SCRATCH.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix="desk-", dir=SCRATCH))
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.agent = self.folder / ("agent.cmd" if os.name == "nt" else "agent")
        fake = REPO / "tests" / "fake_agent.py"
        if os.name == "nt":
            self.agent.write_text(f'@"{sys.executable}" "{fake}" %*\n', encoding="utf-8")
        else:
            self.agent.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8")
            self.agent.chmod(self.agent.stat().st_mode | stat.S_IXUSR)
        # on Windows the agent is started through Python: a .cmd shim would cut the message at its first line
        agent = [sys.executable, str(fake)] if os.name == "nt" else str(self.agent)
        relay.write_json(self.folder / "desk.json", {"agent": agent, "minutes_per_prompt": 1})

    def chat(self, name: str, *said: tuple[str, str]) -> str:
        """A chat in phone mode that has posted these (kind, text) lines."""
        with relay.locked(self.folder):
            found = relay.read_json(self.folder / "chats.json", {})
            found[name] = {"label": f"Chat {name}", "on": True, "since": int(time.time())}
            relay.write_json(self.folder / "chats.json", found)
        for kind, text in said:
            relay.add_event(self.folder, kind, text, chat=name)
        return name

    def look(self) -> None:
        """One look of the desk, and the prompts it took carried out to the end."""
        desk.run_desk(self.folder, once=True)

    def said(self, chat: str, kind: str) -> list[str]:
        return [event["text"] for event in relay.read_feed(self.folder) if event.get("chat") == chat and event["kind"] == kind]


class Finished(DeskCase):
    def test_a_chat_that_posted_its_result_is_finished(self) -> None:
        feed = [{"n": 1, "t": 100, "kind": "update", "from": "pc", "chat": "aaaa"},
                {"n": 2, "t": 110, "kind": "result", "from": "pc", "chat": "aaaa"}]
        self.assertTrue(desk.finished(feed, "aaaa", 120, 1800))

    def test_a_chat_that_just_posted_an_update_is_at_work(self) -> None:
        feed = [{"n": 1, "t": 100, "kind": "result", "from": "pc", "chat": "aaaa"},
                {"n": 2, "t": 110, "kind": "prompt", "from": "pc", "chat": "aaaa"},
                {"n": 3, "t": 115, "kind": "update", "from": "pc", "chat": "aaaa"}]
        self.assertFalse(desk.finished(feed, "aaaa", 120, 1800))

    def test_a_chat_that_went_silent_counts_as_finished(self) -> None:
        feed = [{"n": 1, "t": 100, "kind": "update", "from": "pc", "chat": "aaaa"}]
        self.assertFalse(desk.finished(feed, "aaaa", 100 + 1799, 1800))
        self.assertTrue(desk.finished(feed, "aaaa", 100 + 1801, 1800))

    def test_a_prompt_from_the_phone_does_not_make_a_chat_busy(self) -> None:
        feed = [{"n": 1, "t": 100, "kind": "result", "from": "pc", "chat": "aaaa"},
                {"n": 2, "t": 110, "kind": "prompt", "from": "phone", "chat": "aaaa"}]
        self.assertTrue(desk.finished(feed, "aaaa", 111, 1800))

    def test_another_chat_s_words_do_not_count(self) -> None:
        feed = [{"n": 1, "t": 100, "kind": "result", "from": "pc", "chat": "aaaa"},
                {"n": 2, "t": 110, "kind": "update", "from": "pc", "chat": "bbbb"}]
        self.assertTrue(desk.finished(feed, "aaaa", 111, 1800))
        self.assertTrue(desk.finished(feed, "cccc", 111, 1800))  # a chat that said nothing yet


class Holding(DeskCase):
    def held(self, chat: str) -> bool:
        return (self.folder / f"listening-{chat}").exists()

    def test_it_holds_finished_chats_only_and_lets_go_when_it_ends(self) -> None:
        done = self.chat("done", ("update", "working"), ("result", "All done."))
        busy = self.chat("busy", ("result", "old"), ("update", "at it again"))
        seen = {}
        original = relay.take_prompt

        def spy(folder, chat):  # what is held at the moment the desk asks for a prompt
            seen[chat] = {name: self.held(name) for name in (done, busy)}
            return original(folder, chat)

        relay.take_prompt = spy
        self.addCleanup(setattr, relay, "take_prompt", original)
        self.look()
        self.assertEqual(seen, {done: {done: True, busy: False}})
        self.assertFalse(self.held(done))  # a desk that ended holds nothing

    def test_a_chat_that_waits_itself_keeps_its_own_line(self) -> None:
        chat = self.chat("self", ("result", "Done."))
        (self.folder / f"listening-{chat}").write_text("999999", encoding="ascii")  # the chat's own `listen`
        relay.put_prompt(self.folder, chat, "p1", "for the chat itself")
        self.look()
        self.assertEqual((self.folder / f"listening-{chat}").read_text(encoding="ascii"), "999999")
        self.assertEqual(relay.waiting_ids(self.folder, chat), ["p1"])  # the desk did not take it
        self.assertEqual(self.said(chat, "result"), ["Done."])

    def test_a_chat_that_left_phone_mode_is_not_held(self) -> None:
        chat = self.chat("gone", ("result", "Done."))
        found = relay.read_json(self.folder / "chats.json", {})
        found[chat]["on"] = False
        relay.write_json(self.folder / "chats.json", found)
        relay.put_prompt(self.folder, chat, "p1", "anyone there")
        self.look()
        self.assertEqual(relay.waiting_ids(self.folder, chat), ["p1"])

    def test_a_prompt_for_a_chat_at_work_waits_for_its_result(self) -> None:
        chat = self.chat("work", ("prompt", "do the thing"), ("update", "doing it"))
        relay.put_prompt(self.folder, chat, "p1", "and then this")
        self.look()
        self.assertEqual(relay.waiting_ids(self.folder, chat), ["p1"])
        relay.add_event(self.folder, "result", "The thing is done.", chat=chat)
        self.look()
        self.assertEqual(relay.waiting_ids(self.folder, chat), [])
        self.assertIn("last=and then this", self.said(chat, "result")[-1])


class CarriedOut(DeskCase):
    def test_a_prompt_is_answered_under_the_chat_s_id_with_what_the_chat_said(self) -> None:
        project = self.folder / "project"
        project.mkdir()
        chat = self.chat("proj", ("prompt", "build it"), ("result", f"Built. The file is {project / 'out.txt'}"))
        relay.put_prompt(self.folder, chat, "p1", "Now add a README")
        self.look()
        answer = self.said(chat, "result")[-1]
        self.assertIn("last=Now add a README", answer)
        self.assertIn("session=ses_fake1", answer)
        self.assertNotIn("Let me look.", answer)  # only the last message is the answer
        if not str(project).startswith(str(Path.home())):
            self.skipTest("the scratch folder is outside the home folder: the work folder is not taken from the feed")
        self.assertIn(f"cwd={project.resolve()}", answer.replace("/private", ""))

    def test_the_next_prompt_goes_on_in_the_same_session(self) -> None:
        chat = self.chat("same", ("result", "First answer."))
        relay.put_prompt(self.folder, chat, "p1", "one")
        self.look()
        kept = relay.read_json(self.folder / "desk-sessions.json", {})[chat]
        self.assertEqual(kept["session"], "ses_fake1")
        relay.write_json(self.folder / "desk-sessions.json", {chat: {**kept, "session": "ses_kept9"}})
        relay.put_prompt(self.folder, chat, "p2", "two")
        self.look()
        self.assertIn("session=ses_kept9", self.said(chat, "result")[-1])

    def test_what_the_agent_is_told(self) -> None:
        chat = self.chat("told", ("prompt", "typed at the computer"), ("result", "The chat's answer."))
        relay.add_event(self.folder, "prompt", "the new one", source="phone", id="p9", chat=chat)
        feed = relay.read_feed(self.folder)
        message = desk.told(feed, chat, "Chat told", {"id": "p9", "text": "the new one"}, 0)
        self.assertIn("The chat's answer.", message)
        self.assertIn("He (at the computer), prompt: typed at the computer", message)
        self.assertEqual(message.count("the new one"), 1)  # his prompt once, under HIS PROMPT
        self.assertTrue(message.rstrip().endswith("HIS PROMPT\nthe new one"))
        later = desk.told(feed, chat, "Chat told", {"id": "p9", "text": "the new one"}, feed[-2]["n"])
        self.assertNotIn("The chat's answer.", later)  # a session that goes on is told only what is new

    def test_a_photo_is_passed_on(self) -> None:
        chat = self.chat("snap", ("result", "Done."))
        photo = self.folder / "photo.jpg"
        photo.write_bytes(b"jpeg")
        relay.put_prompt(self.folder, chat, "p1", "what is this", image=str(photo))
        self.look()
        self.assertIn("files=1", self.said(chat, "result")[-1])

    def test_an_agent_that_fails_is_reported_as_an_error(self) -> None:
        chat = self.chat("fail", ("result", "Done."))
        relay.put_prompt(self.folder, chat, "p1", "FAIL please")
        self.look()
        self.assertEqual(self.said(chat, "result"), ["Done."])
        self.assertIn("the model said no", self.said(chat, "error")[-1])

    def test_an_agent_that_says_nothing_is_reported_as_an_error(self) -> None:
        chat = self.chat("mute", ("result", "Done."))
        relay.put_prompt(self.folder, chat, "p1", "SILENT please")
        self.look()
        self.assertIn("ended with code 3 and no answer", self.said(chat, "error")[-1])

    def test_no_agent_on_this_computer_is_said_on_the_phone(self) -> None:
        relay.write_json(self.folder / "desk.json", {"agent": str(self.folder / "nothing-here")})
        chat = self.chat("none", ("result", "Done."))
        relay.put_prompt(self.folder, chat, "p1", "hello")
        self.look()
        self.assertIn("has no agent to run", self.said(chat, "error")[-1])

    def test_a_desk_that_is_stopped_ends_the_prompt_it_works_on(self) -> None:
        chat = self.chat("slow", ("result", "Done."))
        stop = threading.Event()
        job = threading.Thread(
            target=desk.carry_out,
            args=(self.folder, chat, "Chat slow", {"id": "p1", "text": "SLOW please"}, desk.desk_settings(self.folder), stop),
        )
        job.start()
        time.sleep(1.5)
        stop.set()
        job.join(timeout=20)
        self.assertFalse(job.is_alive())
        self.assertIn("was stopped while it worked", self.said(chat, "error")[-1])


class AboutAndHome(DeskCase):
    """2026-11-24: a chat whose answer said "~20:25" (about 20:25) failed every next prompt with
    'Could not determine home directory.': the ~ was read as the home folder of a user "20:25"."""

    def test_a_tilde_that_means_about_is_no_path(self) -> None:
        self.assertEqual(desk.PATH_IN_TEXT.findall("read just now (24 Nov, ~20:25), ~22 tasks, ~59 min"), [])
        self.assertEqual(desk.PATH_IN_TEXT.findall("see ~/Projects/x and ~\\Work\\y"), ["~/Projects/x", "~\\Work\\y"])

    def test_a_chat_that_said_about_a_time_still_takes_the_next_prompt(self) -> None:
        chat = self.chat("tild", ("result", "Read from Task Scheduler (24 Nov, ~20:25). Approx ~81 tasks + ~12 items, ~59 min."))
        relay.put_prompt(self.folder, chat, "p1", "which r in asus, which r in mac?")
        self.look()
        self.assertEqual(self.said(chat, "error"), [])
        self.assertIn("last=which r in asus, which r in mac?", self.said(chat, "result")[-1])

    def test_no_known_home_folder_falls_back_to_the_desk_s_own(self) -> None:
        feed = [{"n": 1, "t": 1, "kind": "result", "from": "pc", "chat": "home", "text": "see ~/Projects/x and ~bob/y"}]
        with mock.patch.object(Path, "home", side_effect=RuntimeError("Could not determine home directory.")), \
                mock.patch.object(Path, "expanduser", side_effect=RuntimeError("Could not determine home directory.")):
            self.assertEqual(desk.work_folder(feed, "home", desk.desk_settings(self.folder), self.folder), self.folder / "desk-work")


class LiveWork(DeskCase):
    """What the desk is doing for a chat travels to the phone while it works, and is gone after."""

    def test_the_steps_show_while_the_desk_works_and_go_when_it_is_done(self) -> None:
        chat = self.chat("live", ("result", "Done."))
        (self.folder / "desk.pid").write_text(str(os.getpid()), encoding="ascii")
        work = desk.Work(self.folder, chat, "OpenCode", "started in C:\\x")
        work.step("shell: git status")
        work.step("shell: git status")  # the same step twice is one step
        seen = [entry for entry in relay.chat_list(self.folder) if entry["id"] == chat][0]["work"]
        self.assertEqual(seen["app"], "OpenCode")
        self.assertEqual([step[1] for step in seen["steps"]], ["started in C:\\x", "shell: git status"])
        for number in range(relay.WORK_STEPS + 3):
            work.step(f"step {number}")
        self.assertEqual(len(relay.desk_work(self.folder, chat)["steps"]), relay.WORK_STEPS)
        (self.folder / "desk.pid").unlink()
        self.assertIsNone(relay.desk_work(self.folder, chat))  # left behind by a desk that is gone

    def test_after_a_prompt_nothing_is_left_at_work(self) -> None:
        chat = self.chat("gone", ("result", "Done."))
        relay.put_prompt(self.folder, chat, "p1", "hello")
        self.look()
        self.assertTrue(self.said(chat, "result"))
        self.assertFalse((self.folder / f"working-{chat}.json").exists())


class ChosenInCairn(DeskCase):
    """Cairn says which computer carries a prompt out, and which app."""

    def cairn(self, line: str) -> None:
        kept = relay.read_json(self.folder / "desk.json", {})
        kept.update(cairn=[sys.executable, str(REPO / "tests" / "fake_cairn.py")], cairn_env={"FAKE_CAIRN_PHONE": line})
        relay.write_json(self.folder / "desk.json", kept)

    def test_without_cairn_it_is_this_computer_and_opencode(self) -> None:
        self.assertEqual(desk.route(desk.desk_settings(self.folder)), ("", "opencode"))

    def test_the_route_is_read_from_cairn(self) -> None:
        self.cairn("PHONE to=ASUS EXPERTBOOK P5405CSA agent=opencode")
        self.assertEqual(desk.route(desk.desk_settings(self.folder)), ("ASUS EXPERTBOOK P5405CSA", "opencode"))
        self.cairn("PHONE to=ASUS EXPERTBOOK P5405CSA agent=claude")  # an app the desk does not know: nothing is sent away
        self.assertEqual(desk.route(desk.desk_settings(self.folder)), ("", "opencode"))
        self.cairn("PHONE to=here agent=opencode")
        self.assertEqual(desk.route(desk.desk_settings(self.folder)), ("", "opencode"))
        self.cairn("something else")
        self.assertEqual(desk.route(desk.desk_settings(self.folder)), ("", "opencode"))

    def test_here_with_opencode_runs_the_agent_as_before(self) -> None:
        self.cairn("PHONE to=here agent=opencode")
        chat = self.chat("here", ("result", "Done."))
        relay.put_prompt(self.folder, chat, "p1", "still local")
        self.look()
        self.assertIn("session=ses_fake1", self.said(chat, "result")[-1])

    def test_a_prompt_for_another_computer_goes_through_cairn_and_comes_back(self) -> None:
        self.cairn("PHONE to=ASUS EXPERTBOOK P5405CSA agent=opencode")
        chat = self.chat("away", ("result", "Done here."))
        relay.put_prompt(self.folder, chat, "p1", "do it over there")
        self.look()
        self.assertEqual(self.said(chat, "result")[-1],
                         "from=ASUS EXPERTBOOK P5405CSA app=opencode title=Phone prompt: Chat away last=do it over there")
        self.assertIn("Sent to ASUS EXPERTBOOK P5405CSA: Cairn there starts OpenCode on it", self.said(chat, "update")[-1])

    def test_another_computer_that_does_not_answer_is_an_error_with_its_reason(self) -> None:
        self.cairn("PHONE to=ASUS EXPERTBOOK P5405CSA agent=opencode")
        chat = self.chat("late", ("result", "Done here."))
        relay.put_prompt(self.folder, chat, "p1", "SLOW please")
        self.look()
        self.assertEqual(self.said(chat, "result"), ["Done here."])
        self.assertIn("OpenCode on ASUS EXPERTBOOK P5405CSA did not carry this out: ASUS EXPERTBOOK P5405CSA has not answered", self.said(chat, "error")[-1])


class Commands(DeskCase):
    def test_status_and_feed_run(self) -> None:
        chat = self.chat("stat", ("result", "Done."))
        self.assertEqual(desk.command_status(self.folder), 0)
        self.assertEqual(desk.command_feed(self.folder, chat, 5), 0)
        self.assertEqual(desk.command_feed(self.folder, "zzzz", 5), 2)

    def test_install_on_windows_writes_the_logon_item_and_starts_the_desk_outside_the_job(self) -> None:
        """Windows cannot be run here: its registry and its process start are stood in for."""
        written, started = {}, []

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *ignored):
                return False

        registry = types.SimpleNamespace(
            HKEY_CURRENT_USER="HKCU", REG_SZ=1, KEY_SET_VALUE=2,
            CreateKey=lambda root, path: Key(), OpenKey=lambda root, path, reserved, access: Key(),
            SetValueEx=lambda key, name, reserved, kind, value: written.update({name: value}),
            DeleteValue=lambda key, name: written.pop(name),
        )

        def start(command, **options):
            started.append((command, options))
            (self.folder / "desk.pid").write_text(str(os.getpid()), encoding="ascii")  # a desk that came up

        environment = {name: value for name, value in os.environ.items() if name != "VECTOR_RELAY_HOME"}
        environment["RUNNER_TRACKING_ID"] = "github_job_1"
        with mock.patch.dict(sys.modules, {"winreg": registry}), mock.patch.object(desk, "on_windows", return_value=True), \
                mock.patch.object(desk.subprocess, "Popen", side_effect=start), mock.patch.dict(os.environ, environment, clear=True):
            self.assertEqual(desk.command_install(self.folder), 0)
            command = written[desk.RUN_NAME]
            self.assertTrue(command.endswith(" run") and "desk.py" in command, command)
            self.assertEqual(len(started), 1)
            self.assertEqual(started[0][0][-1], "run")
            self.assertNotIn("RUNNER_TRACKING_ID", started[0][1]["env"], "or the runner ends the desk with its job")
            self.assertEqual(started[0][1]["env"]["VECTOR_RELAY_HOME"], str(self.folder))
            self.assertEqual(desk.command_install(self.folder), 0)
            self.assertEqual(len(started), 1, "a desk that runs is not started a second time")
            with mock.patch.object(desk.os, "kill"):
                self.assertEqual(desk.command_remove(self.folder), 0)
            self.assertNotIn(desk.RUN_NAME, written)

    def test_a_tool_is_one_plain_line(self) -> None:
        self.assertEqual(desk.doing({"tool": "shell", "state": {"title": "shell", "input": {"command": "ls  -1"}}}), "shell: ls -1")
        self.assertEqual(desk.doing({"tool": "read", "state": {"title": "README.md"}}), "read: README.md")
        self.assertEqual(desk.doing({}), "working")


if __name__ == "__main__":
    unittest.main()
