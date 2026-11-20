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

---

# Run of 2026-11-20 (/hone, Loop mode)

Maker: Claude in Vector. Judge: council (Pega-Vision, Pega-Thinking, GLM-5.3-Flash). Ask: "push it and /hone too",
right after 1.2.0 (a computer is added by typing 4 digits) was deployed.

## Run queue

- [x] Push 1.2.0: branch `pair-code`, PR #1 merged to `main` (`4f46344`), tag `v1.2.0`
- [x] Rubric vr-2, frozen before scoring
- [x] Baseline score of 1.2.0
- [x] Round 1: version 1.2.1
- [x] Gates G1-G6 on the new version
- [x] Council score and side-by-side vote
- [x] Ship: PR, merge, tag `v1.2.1`, deployed page, installed copy and skill on this PC
- [x] Version history inside the phone page (1.2.2), asked for by the sync step
- [x] Sync: profile README, portfolio card, landing page, Obsidian note and log

## Assumptions

- Assumed: "push it" means the 1.2.0 work goes to the public repo as a pull request, merged, with a tag.
- Assumed: /hone means the improve loop on this app. A new rubric (vr-2) was written because vr-1 was already at
  9.55 and the goal has moved: two laptops on one phone, and 4 digits instead of a long link.
- Assumed: the working copy for this run is a fresh clone on drive C, on its own branch (`agent/hone-1120`).
- Assumed: the result is version 1.2.1 (fixes and wording, nothing new to learn).

## Rubric vr-2 (frozen before scoring)

Hard gates:

| Gate | Check | Result on 1.2.1 |
|---|---|---|
| G1 | Unit tests all pass | 60 of 60 |
| G2 | Phone page end to end against the in-memory relay (WebKit as iPhone 14, Chromium as Pixel 7) | 67 of 67 steps |
| G3 | The same steps against the deployed page and relay, after the deploy | 64 of 64 steps |
| G4 | Nothing private in the repo: no key, no settings, no private addresses or personal folders, no old names | `git grep` finds none |
| G5 | The copy installed on this PC is the version in the repo, and its skill is the repo's skill | `status` says 1.2.1; page files and skill copied from the repo |
| G6 | A real pairing on this PC is added by its 4 digits on the deployed page (WebKit as iPhone), and the phone then names the computer and says what to do next without an error | 5 of 5: added in 5.1 s; 21 s after the tap the phone named the laptop and the next step |

Soft criteria (0-10):

- C1 Adding a computer (w=0.30): 4 = a long link must be pasted or the steps are easy to get wrong; 7 = 4 digits
  work, with rough spots (extra output to read past, no way to take a code back, a confusing message right after
  linking, vague errors); 10 = one phrase in a chat shows 4 digits and nothing else to read, the phone asks only
  for a name and the digits, right after linking the phone names the computer and says the next step in plain
  words, a wrong or dead code is explained, a code can be taken back, and the link and QR ways still work.
- C2 Two laptops on one phone (w=0.25): 4 = one computer per page, or he must keep two pages; 7 = a picker holds
  several computers but it is unclear which one a prompt goes to, or a second laptop's setup is not written down;
  10 = one page holds every computer, the page always says which computer a prompt goes to, a prompt reaches that
  computer and no other, rename works, and the steps to join a second laptop (any OS) are written so he can do it
  in two minutes.
- C3 Safety of the code (w=0.25): 4 = the 4 digits act as a password that the relay or a stranger can try many
  times; 7 = tries and time are limited but the relay could learn the key, or what a stranger can still do is not
  said; 10 = the key never travels and the relay cannot learn it from what it carries, tries and time are limited
  by the computer itself, a used or dead code opens nothing, the agent is told what it must never print, and the
  risk that is left is given as a number.
- C4 Honest, current docs (w=0.20): 4 = the README is behind the code or hides limits; 7 = current, but a limit,
  an unchecked item or a version change is vague; 10 = every command, limit and unchecked item is stated plainly
  with its reason, the versions and what changed are listed, and the numbers in the README match the checks.

Constants: MAX_ROUNDS 5, MIN_GAIN 0.3, DONE_SCORE 8.5.

## Rounds

| round | change | gates | judge score | pairwise win | kept? | note |
|---|---|---|---|---|---|---|
| v0 | 1.2.0 as pushed | G1-G3 pass (57 unit, 64 local, 61 live) | 8.0 (C1 7, C2 8, C3 10, C4 7) | - | baseline | council-judged (3 judges), totals 8.0-8.5, no split criterion |
| 1 | 1.2.1: `pair --code` prints the code and one line, nothing else; `pair --no-code` takes a code back; a second computer waits up to 20 s for its turn instead of being refused; the phone names a newly added computer and the next step instead of a red error; README: numbered second-laptop steps for any system, a picture of the phone right after adding, a list of versions | G1-G6 pass | 9.45 (C1 9, C2 9, C3 10, C4 10) | 3-0 over v0 | yes | council-judged (3 judges), totals 9.25-9.5, no split criterion |
| 2 | README now says things the app already did but the README left out, each found by a judge: what **Rename** does, what each other command does, what `pair --local` is, and the sentences the phone shows when a code is wrong, has run out or gets no answer | text only | not scored again | - | yes | - |
| 3 | 1.2.2: a Version history button on the phone page (every version and what changed), fed by one list, `docs/versions.json`; `CHANGELOG.md` and the README's Versions part are made from it; a test fails when the version moves without a newest entry | G1-G5 pass (64 unit, 68 local, 65 live) | not judged (the sync step's version-history gate) | - | yes | `check_versions.py` 8 of 8 |

Found by the checks, and fixed inside round 1:

- G6 on the real laptop showed a line the simulated steps had missed. The laptop had old chats that left phone mode
  earlier in the day, so the phone said "This chat left phone mode. On the PC, type /phone in a chat." with no
  computer name. It now says "This chat left phone mode. No chat on ASUS laptop is in phone mode now: on that
  computer, type /phone in a chat." A step for this was added to the end-to-end run.
- One local end-to-end run failed for a reason outside the app: two test folders drew the same guard port for
  their helpers. The run now draws again when two folders collide.

Judge fixes not taken, and why:

- Remove the one line under the code: it says where on the phone to type the digits; without it a first-time user
  has a number and no place to put it.
- No manual skill edit on a second laptop: the skill must hold the path of `vector_relay.py` on that computer, and
  that path is the user's choice. Two lines to change, written in the README.
- Time the second-laptop steps: needs the second laptop itself; listed under "not yet checked".

stop: done. All six gates pass and the council score is 9.45, above DONE_SCORE 8.5. Round 3 came from the sync
step, which asks every app to show its version history inside the app; G6 was not run again for it (the pairing
code did not change).

Not checked: a real iPhone (WebKit on Windows stands in for it). The MacBook has not joined
vector-relay.pages.dev yet.
