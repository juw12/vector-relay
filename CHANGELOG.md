# Changelog

Made from `docs/versions.json` by `scripts/make_changelog.py`. Do not edit by hand.
The phone page shows the same list under **Version history**.

## 1.5.0 - 2026-11-25: New chat from the phone, and what the desk is doing while it works

- **New chat** at the top of the chat list: the first prompt opens a chat in phone mode on that computer, and its phone desk carries it out. The chat joins the list under the first words of the prompt. A computer whose desk does not run says so instead of taking the prompt.
- While the desk works on a prompt, the chat shows a live card: the app, how long it has worked, and its last steps (each tool it uses). The status line says **The desk is working on it**, and All chats shows the last step. `status` and `desk.py status` say the same on the computer.
- Fixed: a chat whose answer said `~20:25` (about 20:25) failed every next prompt with **Could not determine home directory**: the desk read the `~` as a home folder. A `~` now counts as a path only before a slash, and an odd one is skipped.
- `agent` in `desk.json` may be a list (a program and its first arguments). The desk's tests run on Windows.

## 1.4.2 - 2026-11-22: The buttons under Device are tall enough for a finger ([#16](https://github.com/juw12/vector-relay/pull/16))

- **Add device with a code** and the buttons beside a device are 48 px high, where they were 42.

## 1.4.1 - 2026-11-22: The phone desk installs on Windows ([#15](https://github.com/juw12/vector-relay/pull/15))

- `desk.py install` works on Windows: the desk starts at every logon (this user's Run key, no administrator needed) and now, without a console window and outside the job that started it. `desk.py remove` takes the logon item away.

## 1.4.0 - 2026-11-22: No chat waits for the phone, and a finished chat can still be prompted ([#13](https://github.com/juw12/vector-relay/pull/13))

- The phone desk (`desk.py`): one background process per computer takes the next prompt for every chat in phone mode that has finished, and has it carried out. A chat posts its result and ends its turn; it no longer sits in `listen`.
- `listen` ends at once with `OFF` unless it is run with `--asked`, for a chat that was told to wait for the phone. The chat stays in phone mode.
- The page says **Finished 18:40** for a chat whose last word is its result, and **Finished 18:40 · send a prompt any time** while the desk holds it. Before, it said the chat was working and, after 30 minutes, that it may have stopped.
- All chats marks such a chat finished and keeps it in the list until Remove.
- `status` on the computer uses the same words.
- The skill: phone mode goes on by itself at the start of a chat that does real work, and waiting is only for a chat that was told to wait.

## 1.3.4 - 2026-11-22: Remove waits longer for a busy computer ([#9](https://github.com/juw12/vector-relay/pull/9))

- Remove from the list gives a computer 20 seconds to say it removed the row, where it gave 9. A busy computer behind an office proxy took longer than 9: the page said it did not answer, and the row left a moment later all the same.

## 1.3.3 - 2026-11-21: The computers answer again, and a row can be removed ([#8](https://github.com/juw12/vector-relay/pull/8))

- Fixed: no computer answered the page for hours. The relay's host cut the requests the relay held for 20 seconds (Cloudflare error 1102, shown as 503). The relay now holds a request for 8 seconds at most and looks into its database at most 10 times in one request; the helper asks for 8 seconds and, when a request is cut all the same, asks again at once with half the hold.
- The page looks again when a request for an answer is cut, where it showed an error before.
- All chats: an open row has **Remove from the list**, with a second tap to confirm. A chat leaves phone mode and the list; a session note is not touched, its computer only stops listing it until its chat writes a new line. The row leaves every computer that lists it, also another computer of the page.
- On the computer: `remove <id>` does the same (`board --ids` prints the ids), `remove` lists the removed session notes and `remove --back <id>` lists one again.
- A removed row leaves the copy the relay keeps within seconds, not after the usual two minutes.

## 1.3.2 - 2026-11-21: One computer, one name on All chats ([#6](https://github.com/juw12/vector-relay/pull/6))

- `board --notes <folder> --this "<name>"` says which name the session notes give this computer. All chats then lists those notes under the name the computer has on the page, where it counted as one more computer before.
- The pictures in the README are drawn under a made-up computer name, never the real name of the computer that ran the checks.

## 1.3.1 - 2026-11-21: All chats on real notes: three fixes ([#5](https://github.com/juw12/vector-relay/pull/5))

- A session note whose log has a line out of order no longer counts as ended tomorrow: it stood on top of Ended, marked "now".
- A chat that was already in phone mode when its computer moved to 1.3.0 is on All chats too.
- A Mac whose network name is its address shows under its computer name on the page, not as "192".
- The unit tests no longer fail now and then on a computer where Vector Relay is in use: a test folder that shares its guard port with the running helper is not used.

## 1.3.0 - 2026-11-21: All chats: what every chat on every computer is doing ([#4](https://github.com/juw12/vector-relay/pull/4))

- All chats on the page: every chat of every computer it knows, the running ones first, each with its last step, how far it is and when. A row opens to its last steps, and one computer can be looked at alone.
- A chat says what it is doing without phone mode: `step --title "..." "..."` starts its row, `step --chat <id> "..."` adds a step, `--end shipped` ends it. A chat in phone mode is listed by itself, and what it says to the phone is its step.
- `board --notes <folder>` also lists the chats written down as session notes in that folder (the format Cairn writes), each under the computer its note names.
- `view` opens the page on the computer itself, already linked, on All chats. `board` prints the same list in the terminal.
- A computer that is asleep still shows what it last said. One with an older Vector Relay shows its chats in phone mode.

## 1.2.2 - 2026-11-20: Version history on the phone ([#3](https://github.com/juw12/vector-relay/pull/3))

- The chat list on the phone has a Version history button: every version and what changed in it.
- One list of versions (docs/versions.json) now feeds the phone page, CHANGELOG.md and the README.

## 1.2.1 - 2026-11-20: The code alone, and the phone names the computer ([#2](https://github.com/juw12/vector-relay/pull/2))

- `pair --code` prints the code alone, with no QR lines to read past.
- `pair --no-code` takes a code back.
- A second computer gets its code right after the first one was used (it was refused for 15 seconds before).
- Right after a computer is added, the phone names it and says the next step, where it showed an error until a chat was in phone mode.
- A computer whose chats all left phone mode shows the same calm line instead of "Your PC does not answer".

## 1.2.0 - 2026-11-20: Add a computer with a 4-digit code ([#1](https://github.com/juw12/vector-relay/pull/1))

- Add a computer on the phone by typing 4 digits instead of scanning a QR code or pasting a link.

## 1.1.0 - 2026-11-20: Several computers on one phone

- Several computers on one phone page (the device picker).
- A local Wi-Fi mode that needs no relay.

## 1.0.0 - 2026-11-19: First version under its own name

- A relay and an address of its own.
- Several chats at once.
- Pictures both ways.
- A chat can wait for the next prompt all day.
