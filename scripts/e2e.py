"""The phone page in a real browser engine, talking to the helper through a relay.

    python scripts/e2e.py                      WebKit as an iPhone, relay rules in memory on this PC
    python scripts/e2e.py --live               the same steps on the deployed page and relay (a
                                               throwaway channel that is wiped at the end)

Needs: pip install playwright; playwright install webkit chromium
Pictures go to shots/.
"""

from __future__ import annotations

import argparse
import functools
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import vector_relay as relay  # noqa: E402
from tests.fake_relay import FakeRelay  # noqa: E402

RESULT = """## Atlas O-ring squeeze

Margin is **0.04 mm** at RSS, see `stack.xlsx`.

| Item | Nominal | Tol |
|---|---|---|
| Groove depth | 1.20 | 0.03 |
| O-ring 線徑 | 1.50 | 0.05 |

1. Worst case fails by 0.01 mm
2. RSS passes

```
python -m tolstack run atlas.yaml
```

Source: [the drawing](https://example.com/drawing?a=1&b=2)
<img src=x onerror="window.hacked=1">
"""

steps: list[tuple[bool, str]] = []


def check(passed: bool, what: str) -> None:
    steps.append((bool(passed), what))
    print(("PASS  " if passed else "FAIL  ") + what, flush=True)


def run(folder: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "vector_relay.py"), *arguments], capture_output=True, timeout=90, encoding="utf-8",
        env={**os.environ, "VECTOR_RELAY_HOME": str(folder), "PYTHONIOENCODING": "utf-8"},
    )


def listen(folder: Path, chat: str) -> subprocess.Popen:
    listener = subprocess.Popen(
        [sys.executable, str(REPO / "vector_relay.py"), "listen", "--wait", "90", "--chat", chat], stdout=subprocess.PIPE,
        encoding="utf-8", env={**os.environ, "VECTOR_RELAY_HOME": str(folder)},
    )
    deadline = time.time() + 10
    while time.time() < deadline and not relay.listening(folder, chat):
        time.sleep(0.1)
    return listener


def draw_plot(path: Path) -> None:
    """A picture the chat would send: a leak-rate plot."""
    from PIL import Image, ImageDraw

    plot = Image.new("RGB", (1800, 1100), "white")
    pen = ImageDraw.Draw(plot)
    pen.rectangle((120, 80, 1720, 980), outline=(60, 60, 60), width=4)
    points = [(120 + 80 * i, 900 - int(700 * (1 - 0.86 ** i))) for i in range(21)]
    pen.line(points, fill=(47, 91, 140), width=10)
    pen.line((120, 330, 1720, 330), fill=(217, 72, 31), width=5)
    for x, y in points:
        pen.ellipse((x - 12, y - 12, x + 12, y + 12), fill=(47, 91, 140))
    plot.save(path)


def take_photo(path: Path) -> None:
    """A photo the phone would send: larger than the page lets through as it is."""
    from PIL import Image

    Image.effect_noise((3000, 2200), 60).convert("RGB").save(path, "JPEG", quality=92)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *arguments) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help=f"use the deployed page and relay at {relay.RELAY}")
    parser.add_argument("--relay", default="", help="a deployed relay to go through instead of the one in memory")
    parser.add_argument("--page", default="", help="a deployed phone page to open instead of docs/ on this PC")
    chosen = parser.parse_args()
    if chosen.live:
        chosen.relay, chosen.page = relay.RELAY, relay.RELAY + "/"

    from playwright.sync_api import expect, sync_playwright

    (REPO / ".tmp").mkdir(exist_ok=True)
    (REPO / "shots").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="e2e-", dir=REPO / ".tmp"))
    fake = None if chosen.relay else FakeRelay()
    site = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(REPO / "docs")))
    threading.Thread(target=site.serve_forever, daemon=True).start()
    page_address = chosen.page or f"http://127.0.0.1:{site.server_address[1]}/"
    slow = 25_000 if chosen.relay else 20_000
    suffix = "-live" if chosen.relay else ""

    try:
        paired = run(folder, "pair", "--relay", chosen.relay or fake.address, "--page", page_address)
        check(paired.returncode == 0, "pair makes a code")
        found = relay.settings(folder)
        address = page_address + "#r=" + relay.ticket(found)
        on = run(folder, "on", "--label", "Atlas FA")
        check(on.returncode == 0 and "chat=" in on.stdout, "phone mode on, helper running")
        atlas = on.stdout.split("chat=")[1][:4]
        helper = relay.Relay(found)
        deadline = time.time() + 30
        while time.time() < deadline and helper.call("GET", "/state")[0] != 200:  # a person takes longer to scan the code
            time.sleep(0.3)
        helper.close()

        with sync_playwright() as playwright:
            webkit = playwright.webkit.launch()
            iphone = playwright.devices["iPhone 14"]
            errors: list[str] = []

            # ---- not linked
            context = webkit.new_context(**iphone)
            page = context.new_page()
            page.goto(page_address)
            expect(page.locator("#none")).to_be_visible()
            check(page.locator("#composer").is_hidden(), "a phone with no code sees how to link, and no prompt box")
            context.close()

            # ---- linked
            context = webkit.new_context(**iphone)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(address)
            expect(page.locator(".item.note .text")).to_have_text("Phone mode on: Atlas FA", timeout=slow)
            check(True, "the page opens the sealed feed: 'Phone mode on: Atlas FA'")
            expect(page.locator("#label")).to_have_text("· Atlas FA")
            expect(page.locator("#status")).to_contain_text("Vector is working", timeout=slow)
            check(True, "status: working while the chat is not listening")
            check("#r=" in page.url, "on an iPhone the key stays in the address, for the Home Screen icon")
            check(page.locator("#hint").is_visible(), "the Add to Home Screen hint shows on an iPhone in the browser")
            check(page.locator("#chats").is_hidden(), "one chat in phone mode: no chat picker in the way")

            listener = listen(folder, atlas)
            expect(page.locator("#status")).to_have_text("Vector is waiting for your prompt", timeout=slow)
            check(True, "status: waiting for your prompt while the chat listens")

            prompt = "更新 Atlas 的 O-ring 結果\nthen tell me the margin <b>now</b>"
            page.locator("#box").fill(prompt)
            page.locator("#send").tap()
            heard, _ = listener.communicate(timeout=40)
            check(heard.split("\n", 2)[2].rstrip("\n") == prompt, "the chat's `listen` prints exactly what was typed on the phone")
            expect(page.locator(".item.prompt .meta")).to_contain_text("picked up", timeout=slow)
            check(page.locator(".item.prompt .words").inner_text() == prompt, "the prompt shows on the phone as typed, marked 'picked up'")
            check(page.locator("#box").input_value() == "", "the prompt box is empty again")

            run(folder, "say", "reading the leak data")
            expect(page.locator(".item.update .text")).to_have_text("reading the leak data", timeout=slow)
            check(True, "an update from the chat shows on the phone")

            note = folder / "result.md"
            note.write_text(RESULT, encoding="utf-8")
            run(folder, "say", "--kind", "result", "--file", str(note))
            result = page.locator(".item.result .text")
            expect(result.locator("h3")).to_have_text("Atlas O-ring squeeze", timeout=slow)
            check(result.locator("table tr").count() == 3 and result.locator("th").count() == 3, "result: the table is a table")
            check(result.locator("ol li").count() == 2 and result.locator("pre code").count() == 1, "result: the list and the code block")
            check(result.locator("strong").first.inner_text() == "0.04 mm", "result: bold text")
            link = result.locator("a")
            check(link.get_attribute("href") == "https://example.com/drawing?a=1&b=2" and link.get_attribute("rel") == "noopener noreferrer",
                  "result: the link opens outside, with its address unchanged")
            check(result.locator("img").count() == 0 and page.evaluate("window.hacked") is None, "result: HTML in the text stays text")
            page.screenshot(path=str(REPO / "shots" / f"phone-light{suffix}.png"))

            long = folder / "long.md"
            long.write_text("Row of data. " * 700, encoding="utf-8")
            run(folder, "say", "--kind", "result", "--file", str(long))
            more = page.locator(".more")
            expect(more).to_be_visible(timeout=slow)
            more.tap()
            expect(more).to_have_count(0, timeout=slow)
            shown = page.locator(".item.result .text").last.inner_text()
            check(len(shown) >= len("Row of data. " * 700) - 2, f"'Show all' brings the whole text ({len(shown)} characters)")

            # ---- a picture from the chat
            plot = folder / "plot.png"
            draw_plot(plot)
            said = run(folder, "say", "--kind", "result", "--image", str(plot), "Leak rate against squeeze: the limit is the orange line.")
            check("ok n=" in said.stdout, "the chat posts a picture with `say --image`")
            picture = page.locator(".item.result .pic img").last
            expect(picture).to_be_visible(timeout=slow * 2)
            page.wait_for_function("document.querySelector('.item.result:last-of-type .pic img').naturalWidth > 0", timeout=slow)
            width = picture.evaluate("image => image.naturalWidth")
            check(width == 1600, f"the picture opens on the phone, made smaller for the trip ({width} px wide)")
            page.screenshot(path=str(REPO / "shots" / f"phone-picture{suffix}.png"))

            # ---- a photo from the phone
            photo = folder / "photo.jpg"
            take_photo(photo)
            listener = listen(folder, atlas)
            page.locator("#photo").set_input_files(str(photo))
            expect(page.locator("#preview")).to_be_visible(timeout=slow)
            page.locator("#box").fill("what is wrong with this part?")
            page.locator("#send").tap()
            heard, _ = listener.communicate(timeout=90)
            lines = heard.splitlines()
            kept = Path(lines[2][6:]) if len(lines) > 3 and lines[2].startswith("IMAGE ") else None
            check(kept is not None and kept.read_bytes()[:3] == b"\xff\xd8\xff" and lines[3] == "what is wrong with this part?",
                  "a photo taken on the phone reaches the chat as a file, with the words typed under it")
            if kept is not None:
                from PIL import Image

                with Image.open(kept) as arrived:
                    check(max(arrived.size) <= 1600 and kept.stat().st_size < 600_000,
                          f"the photo was made small on the phone first ({arrived.size[0]}x{arrived.size[1]}, {kept.stat().st_size // 1000} kB from {photo.stat().st_size // 1000} kB)")
            expect(page.locator(".item.prompt .meta").last).to_contain_text("picked up", timeout=slow * 2)
            check(page.locator(".item.prompt .pic img").count() == 1 and page.locator("#preview").is_hidden(), "the photo shows in the sent prompt, and the preview is gone")

            # ---- a second chat in phone mode
            on = run(folder, "on", "--label", "Weekly report")
            weekly = on.stdout.split("chat=")[1][:4]
            expect(page.locator("#chats .chip")).to_have_count(2, timeout=slow)
            check(page.locator(".chip.chosen .name").inner_text() == "Atlas FA", "a second chat goes into phone mode: both show as chips, the first stays chosen")
            run(folder, "say", "--chat", weekly, "--kind", "result", "Slide 3 is updated from today's mail.")
            expect(page.locator(".chip:not(.chosen) .new")).to_be_visible(timeout=slow)
            check(page.locator(".item", has_text="Slide 3 is updated").count() == 0, "what the other chat says is marked on its chip, and stays out of this chat's feed")
            page.screenshot(path=str(REPO / "shots" / f"phone-chats{suffix}.png"))
            page.locator(".chip:not(.chosen)").tap()
            expect(page.locator(".item.result .text")).to_have_text("Slide 3 is updated from today's mail.", timeout=slow)
            check(page.locator(".item", has_text="Atlas O-ring squeeze").count() == 0 and page.locator(".chip .new").count() == 0,
                  "a tap on the chip shows that chat alone, and the mark is gone")
            both = [listen(folder, atlas), listen(folder, weekly)]
            expect(page.locator("#status")).to_have_text("Vector is waiting for your prompt", timeout=slow)
            page.locator("#box").fill("add the TA slide too")
            page.locator("#send").tap()
            heard, _ = both[1].communicate(timeout=40)
            time.sleep(2)
            check("add the TA slide too" in heard and f"chat={weekly}" in heard and both[0].poll() is None,
                  "a prompt goes to the chosen chat only: the other chat keeps waiting")
            run(folder, "off", "--chat", weekly)
            expect(page.locator("#status")).to_contain_text("This chat left phone mode", timeout=slow)
            check(page.locator("#send").is_disabled(), "that chat leaves phone mode: the phone says so and does not let a prompt go nowhere")
            page.locator(".chip", has_text="Atlas FA").tap()
            expect(page.locator("#status")).to_have_text("Vector is waiting for your prompt", timeout=slow)
            page.locator("#box").fill("back to atlas")
            page.locator("#send").tap()
            heard, _ = both[0].communicate(timeout=40)
            check("back to atlas" in heard and f"chat={atlas}" in heard, "back on the first chat, its prompt arrives there")
            results = page.locator(".item.result").count()

            # ---- the PC goes away and comes back
            run(folder, "stop")
            expect(page.locator("#status")).to_contain_text("Your PC does not answer", timeout=45_000)
            check(page.locator(".item.result").count() == results, "PC off: the phone says so and keeps what it had")
            page.locator("#box").fill("are you there?")
            page.locator("#send").tap()
            expect(page.locator(".item.prompt.failed .retry")).to_be_visible(timeout=45_000)
            check(True, "PC off: a prompt is marked not sent, with 'Try again'")
            listener = listen(folder, atlas)
            time.sleep(6)
            check(page.locator(".retry").is_visible() and not relay.waiting_ids(folder),
                  "PC back: the prompt marked not sent is not carried out by itself")
            page.locator(".retry").tap()
            heard, _ = listener.communicate(timeout=60)
            check("are you there?" in heard, "PC back: 'Try again' delivers the prompt, once")
            expect(page.locator("#status")).not_to_contain_text("does not answer", timeout=slow)
            check(len(relay.waiting_ids(folder)) == 0, "nothing is left waiting in the inbox")

            # ---- a fresh phone that only has the relay's copy
            run(folder, "stop")
            if chosen.relay:
                time.sleep(3)
            other = webkit.new_context(**iphone)
            fresh = other.new_page()
            fresh.goto(address)
            expect(fresh.locator("#status")).to_contain_text("Your PC does not answer", timeout=45_000)
            check(fresh.locator(".item").count() >= 1, "PC off, new phone: the relay's sealed copy still shows the last feed")
            other.close()

            # ---- opened from the Home Screen icon: own storage, only the address
            icon = webkit.new_context(**iphone)
            icon.add_init_script(
                "Object.defineProperty(navigator, 'standalone', { get: function () { return true; } });"
                "(function () { var real = Date.now; Date.now = function () { return real() + 3600000; }; }());"
            )
            listener = listen(folder, atlas)
            opened = icon.new_page()
            opened.goto(address)
            expect(opened.locator(".item").first).to_be_visible(timeout=slow)
            check(opened.locator("#hint").is_hidden() and "#r=" in opened.url, "from the Home Screen icon: opens with the key from its address, no hint")
            expect(opened.locator("#status")).to_have_text("Vector is waiting for your prompt", timeout=slow)
            opened.locator("#box").fill("clock test")
            opened.locator("#send").tap()
            heard, _ = listener.communicate(timeout=40)
            check("clock test" in heard, "a phone whose clock is an hour off still gets its prompt through")
            icon.close()

            # ---- dark
            dark = webkit.new_context(**{**iphone, "color_scheme": "dark"})
            night = dark.new_page()
            night.goto(address)
            expect(night.locator(".item.result").first).to_be_visible(timeout=slow)
            night.screenshot(path=str(REPO / "shots" / f"phone-dark{suffix}.png"))
            dark.close()
            context.close()
            webkit.close()

            # ---- another kind of phone: the key leaves the address bar
            chromium = playwright.chromium.launch()
            android = chromium.new_context(**playwright.devices["Pixel 7"])
            pixel = android.new_page()
            pixel.goto(address)
            expect(pixel.locator(".item").first).to_be_visible(timeout=slow)
            check("#r=" not in pixel.url and pixel.locator("#hint").is_hidden(), "on Android the key is taken out of the address")
            pixel.reload()
            expect(pixel.locator(".item").first).to_be_visible(timeout=slow)
            check(True, "on Android a reload still opens the feed (key kept in the browser)")
            android.close()
            chromium.close()

            check(not errors, "no errors on the page" + (": " + "; ".join(errors[:3]) if errors else ""))
    finally:
        forgot = run(folder, "forget")
        if chosen.relay:
            check("The relay forgot this channel." in forgot.stdout, "the throwaway channel is wiped at the relay")
        shutil.rmtree(folder, ignore_errors=True)
        site.shutdown()
        if fake is not None:
            fake.close()

    failed = [what for passed, what in steps if not passed]
    print(f"\n{len(steps) - len(failed)} of {len(steps)} steps pass" + ("" if not failed else "  FAILED: " + "; ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
