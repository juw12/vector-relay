"""The phone page in a real browser engine, talking to the helper through a relay.

    python scripts/e2e.py                      WebKit as an iPhone, relay rules in memory on this PC,
                                               then local mode: the page from this PC over its Wi-Fi address
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
import socket
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


def wifi_address() -> str:
    """This PC's address on its own network, or "" when it has none."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))  # nothing is sent: this only asks which way out would be taken
        found = probe.getsockname()[0]
    except OSError:
        found = ""
    finally:
        probe.close()
    return found if relay.RELAY_ADDRESS.match(f"http://{found}") else ""


def local_trip(playwright, expect) -> None:
    """Local mode: no relay. The page comes from this PC over its Wi-Fi address, as plain http,
    so the browser lends no AES and the page seals with softcrypto.js."""
    host = wifi_address()
    if not host:
        print("SKIP  local mode: this PC has no address on a private network")
        return
    folder = Path(tempfile.mkdtemp(prefix="e2e-local-", dir=REPO / ".tmp"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    try:
        paired = run(folder, "pair", "--local", "--host", host, "--port", str(port))
        check(paired.returncode == 0 and "same Wi-Fi" in paired.stdout, "local mode: pair --local makes a code for this PC's own address")
        found = relay.settings(folder)
        address = found["page"] + "#r=" + relay.ticket(found)
        chat = run(folder, "on", "--label", "Local chat").stdout.split("chat=")[1][:4]
        # an office PC sends everything but its own name through a proxy, which does not know the way back to this PC
        straight = {**os.environ, "NO_PROXY": ",".join(filter(None, [os.environ.get("NO_PROXY", ""), host]))}
        webkit = playwright.webkit.launch(env=straight)
        context = webkit.new_context(**playwright.devices["iPhone 14"])
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.goto(address)
        expect(page.locator(".item.note .text")).to_have_text("Phone mode on: Local chat", timeout=15_000)
        check(page.evaluate("!(window.crypto && window.crypto.subtle)"), "local mode: the page came over plain http, where the browser lends no AES")
        check(True, "local mode: the page from this PC opens the sealed feed with its own AES")
        listener = listen(folder, chat)
        expect(page.locator("#status")).to_have_text("Vector is waiting for your prompt", timeout=15_000)
        prompt = "本機模式 works?\nsecond line"
        page.locator("#box").fill(prompt)
        sent = time.time()
        page.locator("#send").tap()
        heard, _ = listener.communicate(timeout=30)
        took = time.time() - sent
        check(heard.split("\n", 2)[2].rstrip("\n") == prompt, f"local mode: a prompt reaches the chat with no relay ({took:.1f} s)")
        expect(page.locator(".item.prompt .meta")).to_contain_text("picked up", timeout=15_000)

        plot = folder / "plot.png"
        draw_plot(plot)
        run(folder, "say", "--chat", chat, "--kind", "result", "--image", str(plot), "The plot.")
        picture = page.locator(".item.result .pic img").last
        expect(picture).to_be_visible(timeout=30_000)
        page.wait_for_function("document.querySelector('.item.result:last-of-type .pic img').naturalWidth > 0", timeout=15_000)
        check(picture.evaluate("image => image.naturalWidth") == 1600, "local mode: a picture from the chat opens on the phone")

        photo = folder / "photo.jpg"
        take_photo(photo)
        listener = listen(folder, chat)
        page.locator("#photo").set_input_files(str(photo))
        expect(page.locator("#preview")).to_be_visible(timeout=15_000)
        page.locator("#box").fill("and a photo")
        page.locator("#send").tap()
        heard, _ = listener.communicate(timeout=60)
        lines = heard.splitlines()
        kept = Path(lines[2][6:]) if len(lines) > 3 and lines[2].startswith("IMAGE ") else None
        check(kept is not None and kept.read_bytes()[:3] == b"\xff\xd8\xff" and lines[3] == "and a photo", "local mode: a photo from the phone reaches the chat")
        page.screenshot(path=str(REPO / "shots" / "phone-local.png"))

        check(not errors, "local mode: no errors on the page" + (": " + "; ".join(errors[:3]) if errors else ""))
        run(folder, "stop")  # from here the browser itself reports requests that fail
        expect(page.locator("#status")).to_contain_text("cannot reach your PC", timeout=45_000)
        check(True, "local mode: with the helper stopped the phone says it cannot reach the PC")
        context.close()
        webkit.close()
    finally:
        run(folder, "forget")
        shutil.rmtree(folder, ignore_errors=True)


def device_trip(playwright, expect, relay_address: str = "", page_address: str = "") -> None:
    """Two independent pairings, even with identical chat IDs and labels. Through a deployed relay when one is named."""
    scratch = REPO / '.tmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch, ignore_cleanup_errors=True) as temporary:  # a code's waiter may still sit in its folder
        root = Path(temporary)
        fake = None if relay_address else FakeRelay()
        relay_address = relay_address or fake.address
        site = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(REPO / 'docs')))
        threading.Thread(target=site.serve_forever, daemon=True).start()
        base = page_address or f'http://127.0.0.1:{site.server_port}/'
        if fake is None:
            expect.set_options(timeout=25_000)  # a deployed relay answers slower than the one in memory
        hosts = []
        browser = None
        try:
            for name in ('mac', 'asus'):
                host = root / name
                host.mkdir()
                while relay.helper_running(host):
                    host = Path(tempfile.mkdtemp(dir=root))
                hosts.append(host)
                assert run(host, 'pair', '--relay', relay_address, '--page', base).returncode == 0
                relay.write_json(host / 'chats.json', {'abcd': {'label': 'Identical chat', 'on': False, 'since': int(time.time())}})
                assert run(host, 'on', '--chat', 'abcd', '--label', 'Identical chat').returncode == 0
                assert run(host, 'say', '--chat', 'abcd', '--kind', 'result', name + ' feed').returncode == 0
            mac, asus = hosts
            urls = [base + '#r=' + relay.ticket(relay.settings(host)) for host in hosts]
            browser = playwright.webkit.launch()
            context = browser.new_context(**playwright.devices['iPhone 14'])
            context.add_init_script("Object.defineProperty(navigator, 'standalone', {get: () => true});")
            page = context.new_page()
            page.goto(urls[0])
            expect(page.locator('.item.result .text')).to_have_text('mac feed')
            page.locator('#menu').tap()
            page.locator('#renameDevice').tap()
            page.locator('#deviceName').fill('MacBook')
            page.locator('#saveDevice').tap()
            expect(page.locator('#sendTarget')).to_have_text('To: MacBook')
            page.locator('#addDevice').tap()
            expect(page.locator('#deviceCode')).to_be_visible()
            check(page.locator('#deviceLink').is_hidden(), 'devices: Add device asks for the 4-digit code first, the link is the other way')
            page.locator('#useLink').tap()
            page.locator('#deviceName').fill('ASUS laptop')
            page.locator('#deviceLink').fill(base)
            page.locator('#saveDevice').tap()
            expect(page.locator('#deviceError')).to_be_visible()
            page.locator('#deviceLink').fill(urls[1])
            page.locator('#saveDevice').tap()
            expect(page.locator('#sendTarget')).to_have_text('To: ASUS laptop')
            expect(page.locator('.item.result .text')).to_have_text('asus feed')
            page.locator('#box').fill('ASUS only')
            page.locator('#send').tap()
            expect(page.locator('.item.prompt .meta')).to_contain_text('waiting for the chat')
            assert not relay.waiting_ids(mac)
            assert relay.take_prompt(asus, 'abcd')['text'] == 'ASUS only'
            check(True, 'devices: a prompt sent to the ASUS pairing arrives only there, even with an identical chat ID')
            page.goto('about:blank')
            page.goto(urls[0])
            expect(page.locator('#sendTarget')).to_have_text('To: ASUS laptop')
            expect(page.locator('.item.result .text')).to_have_text('asus feed')
            check(True, 'devices: reopening from the original Mac Home Screen URL remembers the chosen ASUS')
            page.locator('#menu').tap()
            page.locator('#devicePicker').select_option(label='MacBook')
            expect(page.locator('#sendTarget')).to_have_text('To: MacBook')
            expect(page.locator('.item.result .text')).to_have_text('mac feed')
            expect(page.locator('.item.prompt')).to_have_count(0)
            page.locator('#box').fill('Mac only')
            page.locator('#send').tap()
            expect(page.locator('.item.prompt .meta')).to_contain_text('waiting for the chat')
            assert not relay.waiting_ids(asus)
            assert relay.take_prompt(mac, 'abcd')['text'] == 'Mac only'
            check(True, 'devices: switching back restores the Mac feed and sends only to the Mac')
            code_trip(page, expect, root, relay_address, base, hosts)
        finally:
            if browser is not None:
                browser.close()
            for host in hosts:
                run(host, 'stop' if fake else 'forget')  # `forget` wipes a throwaway channel at a deployed relay
            if fake is not None:
                fake.close()
            site.shutdown()
            site.server_close()


def code_trip(page, expect, root: Path, relay_address: str, base: str, hosts: list) -> None:
    """A third computer joins with the 4 digits it shows: no QR code, no link to paste."""
    desk = root / 'desk'
    desk.mkdir()
    hosts.append(desk)
    assert run(desk, 'pair', '--relay', relay_address, '--page', base).returncode == 0
    assert run(desk, 'on', '--label', 'Desk chat').returncode == 0
    assert run(desk, 'say', '--kind', 'result', 'desk feed').returncode == 0
    page.locator('#menu').tap()
    page.locator('#addDevice').tap()
    page.locator('#deviceName').fill('Desk PC')
    page.locator('#deviceCode').fill('12')
    page.locator('#saveDevice').tap()
    expect(page.locator('#deviceError')).to_contain_text('Type the 4 digits')
    page.locator('#deviceCode').fill('1234')
    page.locator('#saveDevice').tap()
    expect(page.locator('#deviceError')).to_contain_text('No computer is showing a code', timeout=20_000)
    check(True, 'code: with no code showing anywhere, the phone says so')

    made = run(desk, 'pair', '--code')
    code = made.stdout.split('Code for the phone: ')[1][:4]
    check(made.returncode == 0 and code.isdigit() and '#r=' not in made.stdout, 'code: `pair --code` shows 4 digits and no link')
    page.locator('#deviceCode').fill(f'{(int(code) + 1) % 10_000:04d}')
    page.locator('#saveDevice').tap()
    expect(page.locator('#deviceError')).to_contain_text('That is not the code. Check the 4 digits', timeout=30_000)
    check(len(relay.settings(desk)['key']) == 43 and page.locator('#sendTarget').inner_text() == 'To: MacBook',
          'code: a wrong code brings nothing, and says so')
    page.locator('#deviceCode').fill(code)
    sent = time.time()
    page.locator('#saveDevice').tap()
    expect(page.locator('#sendTarget')).to_have_text('To: Desk PC', timeout=30_000)
    took = time.time() - sent
    expect(page.locator('.item.result .text')).to_have_text('desk feed')
    check(True, f'code: the right 4 digits add the computer and open its sealed feed ({took:.1f} s)')
    check('a phone was linked with it' in run(desk, 'status').stdout, 'code: the computer knows a phone was linked with the code')
    page.locator('#box').fill('Desk only')
    page.locator('#send').tap()
    expect(page.locator('.item.prompt .meta')).to_contain_text('waiting for the chat')
    listening = [chat for chat in relay.chats(desk) if relay.chats(desk)[chat]['on']]
    assert relay.take_prompt(desk, listening[0])['text'] == 'Desk only'
    assert not relay.waiting_ids(hosts[0]) and not relay.waiting_ids(hosts[1])
    check(True, 'code: a prompt to the computer added by code arrives there and nowhere else')
    page.locator('#menu').tap()
    check(page.locator('#devicePicker option').count() == 3, 'code: the picker lists all three computers')
    page.wait_for_timeout(500)  # the chat list has slid in by then
    page.screenshot(path=str(REPO / 'shots' / 'phone-devices.png'))
    page.locator('#addDevice').tap()
    page.wait_for_timeout(300)
    page.screenshot(path=str(REPO / 'shots' / 'phone-code.png'))
    page.locator('#cancelDevice').tap()


def first_by_code(webkit, iphone, expect, folder: Path, page_address: str, slow: int) -> None:
    """A phone that has no computer yet adds its first one with the 4 digits (page and relay at one address)."""
    made = run(folder, "pair", "--code")
    code = made.stdout.split("Code for the phone: ")[1][:4] if "Code for the phone: " in made.stdout else ""
    check(made.returncode == 0 and code.isdigit(), "code: `pair --code` gets a room at the relay and shows 4 digits")
    context = webkit.new_context(**iphone)
    page = context.new_page()
    try:
        page.goto(page_address)
        page.locator("#firstDevice").tap()
        page.locator("#deviceName").fill("Test PC")
        page.locator("#deviceCode").fill(code)
        sent = time.time()
        page.locator("#saveDevice").tap()
        expect(page.locator(".item.note .text")).to_have_text("Phone mode on: Atlas FA", timeout=slow * 2)
        check(True, f"code: a phone with no computer yet adds its first one with the 4 digits ({time.time() - sent:.1f} s)")
        check(page.locator("#sendTarget").inner_text() == "To: Test PC", "code: the computer has the name typed on the phone")
    finally:
        context.close()
    check("a phone was linked with it" in run(folder, "status").stdout, "code: the computer knows a phone was linked with the code")


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
            if chosen.relay and page_address.rstrip("/") == chosen.relay.rstrip("/"):
                first_by_code(webkit, iphone, expect, folder, page_address, slow)

            # ---- linked
            context = webkit.new_context(**iphone)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(address)
            expect(page.locator(".item.note .text")).to_have_text("Phone mode on: Atlas FA", timeout=slow)
            check(True, "the page opens the sealed feed: 'Phone mode on: Atlas FA'")
            expect(page.locator("#title")).to_have_text("Atlas FA")
            check(page.locator("#box").get_attribute("placeholder") == "Send follow-up", "the bar names the chat, the prompt box reads as in the app")
            expect(page.locator("#status")).to_contain_text("Vector is working", timeout=slow)
            check(True, "status: working while the chat is not listening")
            check("#r=" in page.url, "on an iPhone the key stays in the address, for the Home Screen icon")
            check(page.locator("#hint").is_visible(), "the Add to Home Screen hint shows on an iPhone in the browser")
            check(page.locator("#chats").is_hidden() and page.locator("#menuNew").is_hidden(), "the chat list stays out of the way until its button is tapped")
            page.locator("#menu").tap()
            expect(page.locator("#chats .chip.chosen .name")).to_have_text("Atlas FA")
            check(page.locator(".chip").count() == 1 and page.locator(".chip .spin").count() == 1 and page.locator(".chip .when").inner_text() == "now",
                  "the chat list shows the one chat as the app does: its name, working, 'now'")
            page.locator("#shade").tap(position={"x": 370, "y": 400})
            expect(page.locator("#chats")).to_be_hidden()
            check(True, "a tap beside the chat list closes it")

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
            run(folder, "say", "--chat", weekly, "--kind", "result", "Slide 3 is updated from today's mail.")
            expect(page.locator("#menuNew")).to_be_visible(timeout=slow)
            check(page.locator(".item", has_text="Slide 3 is updated").count() == 0, "what the other chat says is marked on the chat list button, and stays out of this chat's feed")
            page.locator("#menu").tap()
            expect(page.locator(".chip:not(.chosen) .new")).to_be_visible(timeout=slow)
            check(page.locator(".chip.chosen .name").inner_text() == "Atlas FA", "a second chat goes into phone mode: both are in the chat list, the first stays chosen")
            page.locator("#find").fill("week")
            expect(page.locator("#chats .chip")).to_have_count(1)
            check(page.locator(".chip .name").inner_text() == "Weekly report", "'Search chats' narrows the list")
            page.locator("#find").fill("")
            expect(page.locator("#chats .chip")).to_have_count(2)
            page.wait_for_timeout(300)
            page.screenshot(path=str(REPO / "shots" / f"phone-chats{suffix}.png"))
            page.locator(".chip:not(.chosen)").tap()
            expect(page.locator("#chats")).to_be_hidden()
            expect(page.locator("#title")).to_have_text("Weekly report")
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
            page.locator("#menu").tap()
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
            if not chosen.relay:
                local_trip(playwright, expect)
            device_trip(playwright, expect, chosen.relay, chosen.page)
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
