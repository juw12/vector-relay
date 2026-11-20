# Changelog

Made from `docs/versions.json` by `scripts/make_changelog.py`. Do not edit by hand.
The phone page shows the same list under **Version history**.

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
