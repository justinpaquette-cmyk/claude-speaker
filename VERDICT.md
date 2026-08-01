# Verdict

Twelve proposals, reviewed adversarially by Fable in two passes ("assume at least
four of these are bad ideas and the recommendations are wrong"). This is the
one-page version. Detail in [PROPOSALS.md](PROPOSALS.md).

**Your only input needed: pick the next build, or redirect it.**

---

## The table

| # | Idea | My call | Fable | Settled |
|---|---|---|---|---|
| 1 | Test suite + `CLAUDE_DIR` override | Build **first** | Build, but **not first** | **Build — 3rd or 4th** |
| 2 | Demo GIF | Build | Build | **Build** |
| 3 | iPhone reach | Research first | Research, expect a dead end | **Research** |
| 4 | Per-project voices | **Build** | **Defer — worst idea here** | **Defer** |
| 5 | CI | Build **after #1** | Build **now**, not gated | **Build now** |
| 6 | `repeat 2` / N-back | Kill | Kill | **Kill** |
| 7 | Digest roll-up | Research | Research — *free, data exists* | **Research** |
| 8 | Configurable wait thresholds | Kill | Kill | **Kill** |
| 9 | Aging chime escalation | Kill | Kill — my *reasoning* was wrong | **Kill** |
| 10 | Other emulators | Research | Research | **Research** |
| 11 | `/spoken-recap` tinting | Kill | Kill | **Kill** |
| 12 | Homebrew tap | Defer | Defer | **Defer** |

---

## Where Fable disagreed with me

Five real ones. It agreed on 6, 8, 11, 12 and refused to manufacture more —
which is the result I wanted; a clean sweep would have meant the framing failed.

**#4 per-project voices — I said Build, Fable says Defer, and it's right.**
I wrote "three `Popen` call sites, two hours." There are **five** `say`
invocations across three files, and two of them (`speak_on_focus`,
`tts-recap.py`) join several sessions into *one* `say` utterance — one process
can't change voice mid-sentence, so per-session voices mean splitting those into
sequential calls with PID handoff. Call it a day, not two hours. Worse, it's a
tenth settings key, and I killed #8 with the exact argument I didn't apply here.
If differentiation is ever wanted, hash the project name to a voice — a default,
not a knob.

**#5 CI — I gated it on #1, Fable says build it now.** My own justification for
CI was that the Swift and C helpers compile on the *user's* machine and fail
invisibly. `swiftc` and `cc` in CI catch exactly that and need zero tests. I
gated a one-hour win behind a multi-day one for no reason.

**#1 tests — right to build, wrong to rank first, and my facts were off.** I
claimed 15 hardcoded `~/.claude` paths; there are 8, and `speak-response.py`
already centralizes through `CLAUDE_DIR`, so the override is nearly a two-line
change. Fable also says my *trim* was wrong in the second pass: the value isn't
in `sanitize`/`pick_speech` (unchanged for months) but in the stateful gating —
which is now the honest half-day-plus it always was.

**#9 — kill stands, my evidence didn't.** I wrote that the freshness fix means a
stale summary "stops chasing you entirely." Not true, and the commit message
says so: the watcher arms on *any* fresh entry for that terminal and then reads
out **everything** it holds, stale included. Red tabs also persist indefinitely.
Right conclusion, wrong reason.

**#3 — the research is nearly pre-answered.** Continuity mirrors iPhone
notifications *to* the Mac, not Mac notifications to the phone. My "it might make
the feature unnecessary" hope is probably worth zero. Still check it — it's an
hour — but don't plan around it.

## What neither of us put on the list

Fable found four omissions. One of them bit us during this session.

- **Repo/install drift.** The hook runs `~/.claude/scripts/speak-response.py`,
  which had drifted **~290 lines ahead of git** — an entire AskUserQuestion cue
  (purple tab, spoken question headers) that was never committed. My focus fix
  landed in a file nothing executes, and any `install.sh` run would have deleted
  the unversioned feature. Now reconciled by three-way merge and committed.
- **Queue write locking.** `enqueue()` appends lock-free while three
  read-modify-`os.replace` writers race it. The code confesses to the window in
  `drain()`'s own comment. For a README that promises "nothing is ever lost."
- **`uninstall.sh`.** `install.sh` edits `settings.json` and `CLAUDE.md`
  programmatically; uninstall is a hand-run `rm` block.
- **Queue trimming.** Only `tts-recap.py` trims to 200. The README's "(last 200)"
  is true only if you use `rr`.

Fable's read on the drift, which I think is right: **tests would never have
caught it** — pytest stays green while the executed copy diverges. But the
`CLAUDE_DIR` override removes drift's *motive*, since today the installed copy is
the only one you can run without commandeering live state. That's why the ask
feature got built there.

---

## Recommended next build

**`install.sh`: back up before overwrite.** ~15 lines — if the installed file
differs from what's about to be written, copy it to a timestamped `.bak` and say
so. Half an hour.

Fable picked it over the queue race and over the test suite, and defended it on
the grounds that it's the only item on the list addressing a failure that
*actually happened this week*, with a worst case of silently deleting 290 lines
of unversioned work — from a script whose header advertises "Idempotent: safe to
re-run after updates," which was false in the worst possible way during the drift
window. It explicitly rejected drift *detection* (hash manifests, CI comparison)
as maintenance and false-positive cost disproportionate for a one-person repo. A
backup isn't a check: no maintenance, no false positives, never blocks an install.

Then, in order: **queue locking + trim** as one commit series (trim must land
*inside* the lock — shipping it first widens the race), then **CI**, then the
**test suite** re-scoped toward the override and the stateful gating.

---

## Already done this session

Not proposals — these shipped while the review was running.

- **Focus fix** (`071afc2`) — with `collision=hold`, a summary landing in the
  terminal you're watching now speaks on the spot instead of making you click off
  and back. Unfocused is unchanged. Switch-in reads now ignore anything older
  than the red threshold, so hour-old backlog never starts talking.
- **Ask cue backported + put under `hold`** (`796d94e`) — the announcer checked
  mode, `ask_speak`, calls and a busy voice, but never `collision`, and the docs
  called that deliberate. That's how an unfocused JustinBot talked at you with
  `hold` set globally. A question isn't an exception to "nothing speaks at a
  terminal you aren't watching" — it's the loudest kind of unprompted speech
  there is. Now silent under `hold`; purple tab and menu bar carry it.
