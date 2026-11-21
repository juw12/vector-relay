# Changelog

Made from `docs/versions.json` by `scripts/make_changelog.py`. Do not edit by hand.
The phone page shows the same list under **Version history**.

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
