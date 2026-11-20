# Refine log: Vector Relay

Run of 2026-11-19 (/hone). Maker: Claude in Vector. Judge: council (Pega-Vision, Pega-Thinking, GLM-5.3-Flash).

## Run queue

- [x] New name everywhere: Vector Relay (repo, files, page, skill, data folder)
- [x] A relay of its own: one Cloudflare Pages project (page + relay function + D1), shared with no other app
- [x] Limits: several chats at once, pictures both ways, waiting all day in one tool call, no time cap
- [x] No same Wi-Fi: checked from outside networks
- [x] Gates G1-G6
- [x] Council score: baseline, new version, pairwise
- [x] Go live: public repo, deployed page and relay, installed copy on the PC, skill installed, phone code made
- [x] Sync: profile README, portfolio card, landing page, Obsidian note and log

## Assumptions

- Assumed: "fix the limits" means the limits the first version listed: one chat at a time, text only, the waiting cap, and the relay the user had to bring. Limits that come from not reaching into the chat app stay, and are stated.
- Assumed: "for pc stay on its fine" means the PC-must-stay-on limit is accepted as it is.
- Assumed: "no need same wifi" means the phone works on mobile data or any network, through a public address.
- Assumed: "just separate it" means its own relay, its own database, its own address and its own repo.
- Assumed: the address vector-relay.pages.dev on Cloudflare's free plan, the same account as his other pages.
- Assumed: version 1.0.0, MIT, public repo juw12/vector-relay with a fresh history under the new name.
- Assumed: the installed copy lives at E:\Projects\vector-relay and its data at %LOCALAPPDATA%\VectorRelay.

## Rubric vr-1 (frozen before scoring)

Hard gates:

| Gate | Check | Result |
|---|---|---|
| G1 | Unit tests all pass | 39 of 39 |
| G2 | Phone page end to end against the in-memory relay (WebKit as iPhone 14, Chromium as Pixel 7) | 41 of 41 steps |
| G3 | The same steps against the deployed page and relay | 42 of 42 steps |
| G4 | The old names appear nowhere in the repo | `git grep` finds none |
| G5 | Page and relay answer from outside networks | page 200 from France, Hungary, India, Portugal, Azerbaijan; relay API answers from Switzerland, Netherlands, Russia, Turkey (check-host.net) |
| G6 | Real loop with the installed copy: on, listen in one tool call, prompt from a stand-in phone, result and picture back, second chat, off | pass, also when started by the chat app's own Python |

Soft criteria (0-10):

- C1 Limits removed (w=0.35): 4 = one chat, text only, waiting is short or costs the chat turns, or the user must set up a relay or be on one network; 7 = most of these are gone but one real limit of daily use is left or a limit is hidden; 10 = several chats, pictures both ways, waiting all day at no cost, works from any network with nothing to host, and every limit that is left is stated plainly with its reason.
- C2 Phone experience (w=0.25): 4 = readable but the user cannot tell what the chat is doing or whether his prompt arrived; 7 = clear status and delivery marks, some rough spots; 10 = at a glance he knows which chat, what it is doing, whether the prompt arrived, and what to do when the PC is off, on a small screen in light and dark.
- C3 Safety (w=0.20): 4 = traffic is readable by the relay or anyone with the address can prompt the chat; 7 = sealed end to end, some gap left unexplained; 10 = sealed end to end, replay and wrong-chat refused, key never printed, clear rules for the agent, and the risk that is left is said honestly.
- C4 Setup simplicity (w=0.20): 4 = the user must deploy or configure servers or share a relay with another app before first use; 7 = works after a few manual steps that need care; 10 = one command and one QR scan, then one word in a chat; nothing to host, nothing shared.

Constants: MAX_ROUNDS 5, MIN_GAIN 0.3, DONE_SCORE 8.5.

## Rounds

| round | change | gates | judge score | pairwise win | kept? | note |
|---|---|---|---|---|---|---|
| v0 | first version, 0.1.0 | G1, G2 only | 5.75 (C1 4, C2 7, C3 9, C4 4) | - | baseline | council-judged (3 judges), totals 5.75-6.0 |
| 1 | own relay and address, several chats, pictures both ways, all-day waiting with held requests, new name | G1-G5 pass | 9.55 (C1 10, C2 9, C3 10, C4 9) | 3-0 over v0 | yes | council-judged (3 judges), totals 9.0-9.8, no split criterion |
| 2 | README says plainly that the phone does not ring while the page is closed (a limit one judge found unstated) | G1-G5 pass | not scored again | 3-0 over round 1 | yes | text only |
| 3 | a command started by a Python without the packages hands itself to one that has them | G1-G6 pass | not judged (a gate fix) | - | yes | found by G6: the helper died silently when the chat app's own Python started it; test added |

Judge fixes not taken, and why:

- Remove `--show-link`: kept for pairing a phone that cannot scan; the skill forbids it and tests check the key stays out of normal output.
- One installer command: the three pip packages are one line; a packaged installer is more to maintain than it saves for one user.
- Push notifications: an iPhone web page needs a push service and a stored subscription; left out, and the limit is stated.
- A real iPhone test: cannot be done from this PC; stated as not checked.

stop: done. All six gates pass and the council score is 9.55, above DONE_SCORE 8.5.

## Also learned

- A 12-hour `listen` in one tool call returned `NONE` on time: the wait itself holds for 12 hours without the chat taking a turn. (That run was the one that showed the helper problem fixed in round 3.)
