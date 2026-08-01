# Proposals

Twelve ideas that came up while building v0.1.0 and were deliberately kept out
of scope. Each one is written the same way — **what · why it matters · rough
cost · risk · recommendation** — so they can be triaged as a set rather than
argued one at a time.

Every recommendation below is a committed opinion, not a menu. They exist to be
disagreed with.

**Legend:** **Build** = do it · **Build after** = blocked on another item ·
**Research** = the answer changes whether it's worth building · **Kill** = not
worth it unless someone asks · **Defer** = right idea, wrong time.

Baseline for the cost estimates: 1,838 lines across three Python scripts, a C
helper and a Swift helper, plus a 102-line installer. Zero tests. No CI.

**Reviewed.** Fable reviewed this list adversarially and disagreed with five of
the calls; the corrections are folded in below and the outcome is in
[VERDICT.md](VERDICT.md), which is the page to read first.

---

## 1. Test suite + `CLAUDE_DIR` env override

**What.** Replace the 8 hardcoded `~/.claude` paths across the three Python
scripts with a single `CLAUDE_DIR = os.environ.get("CLAUDE_DIR", ...)`, then add
pytest coverage for the pure logic (`sanitize`, `pick_speech`, `_trim`,
`adaptive_cap`, `wait_color`, `parse_color`, the settings-resolution ladder) and
the stateful paths (queue lifecycle, tint record lifecycle, watcher focus
transitions, `main()`'s collision branches with the side effects stubbed).

**Why it matters.** There is no way to exercise this code today except by
running it against live state. That is not a hypothetical cost: twice this
session, testing made real terminals flash and speak test text, because the only
queue available was `~/.claude/tts-queue.jsonl`. The focus fix that shipped
alongside this file had to be verified through a throwaway harness that
monkeypatched module globals — which worked, but only because the harness could
reach in and reassign them. The next person changing the wait ladder or the
collision branches gets no such affordance.

**Rough cost.** Half a day. The env override is nearly free — `speak-response.py`
already centralizes on `CLAUDE_DIR`, so it is that line plus the stray
`~/.claude/sessions` inside `session_name()`, plus 6 sites in the other two
scripts. The tests are where the time goes; the pure
functions are trivial, `main()` needs a fixture that fakes a transcript and stubs
`say`/`osascript`/`afplay`.

**Risk.** Low, and mostly of the "tests calcify the wrong thing" kind — pinning
current behavior as correct when some of it isn't. Mitigated by writing tests
against the documented contract rather than the implementation.

**Recommendation: Build first.** Everything else on this list gets safer and
cheaper afterwards, and #5 is meaningless without it.

---

## 2. Demo GIF in the README

**What.** A short screen recording in the README: three terminals, one finishes,
its tab turns green, the menu bar shows the count, a click reads it out.

**Why it matters.** This is the highest-leverage thing available for getting
anyone else to try it. A tool whose entire output is *audio and color* is close
to unsellable in prose — the README currently spends ~11KB describing an effect
that ten seconds of video would land instantly. It is also the honest test of
whether the product reads clearly, since a GIF can't hide behind explanation.

**Rough cost.** An hour or two, mostly staging: set up three terminals doing
plausible work, record, trim, keep it under ~5MB so GitHub renders it inline.
Audio doesn't survive a GIF, so the captions have to carry the spoken part —
or ship an MP4 instead and accept that it needs a click to play.

**Risk.** Essentially none technically. The real risk is a mediocre GIF being
worse than none — a jittery recording of an unreadable terminal actively sells
against the tool.

**Recommendation: Build.**

---

## 3. iPhone reach

**What.** Already sketched in `TODO.md`. Get a finished-summary signal to the
phone when you've walked away from the desk. Two candidate paths: macOS
Continuity notification mirroring (a Focus/notification settings question, not a
feature), or opt-in push via ntfy/Pushover from the Stop hook.

**Why it matters.** It is the one feature that changes what the tool *is*. Every
other item here improves a thing that only works while you're sitting in front
of the machine. This is also the only proposal that would send summary text off
the machine for the first time — today everything is local `say` and `afplay`,
no network, no accounts, and that property is currently absolute.

**Rough cost.** Continuity: an afternoon of testing and a README section, zero
code. Push: a day for the happy path — one HTTP POST, a config key, an opt-in
gate — plus the design question `TODO.md` already flags and doesn't answer (three
summaries pile up while you're away: three banners, one digest, or nothing?).

**Risk.** High, and not the implementation. Shipping a network path quietly
erodes the "nothing ever leaves this machine" claim that makes the tool easy to
trust and easy to install at work. If it ships it must be opt-in, off by default,
and loudly documented. There's also a real chance Continuity already covers 80%
of the need, in which case building push is pure cost.

**Recommendation: Research first.** Test the Continuity path before writing any
code — it's free and it might make the feature unnecessary.

---

## 4. Per-project voices

**What.** Let a session pick its `say -v` voice, so the ops terminal and the
docs terminal don't sound identical. A `voice` setting resolved through the
existing per-session > global > default ladder, passed to the three `Popen`
call sites.

**Why it matters.** The multi-session case is the tool's actual reason for
existing — the announce-name prefix ("the docs terminal: …") exists precisely
because attribution is the hard part of hearing five terminals. Voice does that
job better than a spoken prefix does, because it lands before the sentence
instead of at the start of it, and it costs no words.

**Rough cost.** Small — perhaps two hours. The settings plumbing already exists
(`SETTABLE` + `resolve_setting`); this is one new key, validation against
`say -v '?'`, and threading it onto the queue entry so a deferred readout uses
the originating session's voice rather than the drainer's.

**Risk.** Low. The main trap is a voice name that isn't installed, which makes
`say` fail silently — needs a validation step at set time and a fallback to the
system voice at speak time.

**Recommendation: Build.**

---

## 5. CI

**What.** GitHub Actions on `macos-latest`: `python3 -m py_compile` on the
scripts, `swiftc` on the badge helper, `cc` on `av-status.c`, and pytest.

**Why it matters.** Three of the five source files are compiled artifacts that
the installer builds on the user's machine. A syntax error in the Swift or C
helper is invisible until someone installs, and degrades to "the badge just
doesn't appear" rather than a visible failure.

**Rough cost.** An hour. One workflow file.

**Risk.** Low. macOS runners are slower and metered, but this repo's push volume
makes that irrelevant.

**Recommendation: Build after #1.** A CI job that only runs `py_compile` is
theater — it proves the file parses, which the pre-commit edit already proved.

---

## 6. `repeat 2` / N-back replay

**What.** Extend the existing `repeat` to reach further back: `repeat 2` for the
one before last.

**Why it matters.** Marginally. `repeat` covers the actual need (missed the last
one), and `/spoken-recap` already replays the whole session queue when you want
more than that. This fills the narrow gap between them.

**Rough cost.** An hour in `repeat-hook.py`.

**Risk.** Low cost, low value — which is its own kind of risk. Every keyword
added to the `repeat`/`rr`/`shh`/`hush`/`stop` vocabulary is a word the user has
to remember and a string the hook has to not misfire on.

**Recommendation: Kill unless asked.**

---

## 7. Digest roll-up

**What.** When several terminals finish close together, speak one utterance
("three terminals finished: docs, ops, and the migration") instead of reading
each in turn.

**Why it matters.** The failure mode it addresses is real and gets worse the more
the tool succeeds: `follow` mode with five sessions finishing in a burst is a
solid minute of talking you can't skim. The menu-bar badge already does exactly
this collapse visually (`🟡 2 waiting`), so the pattern is proven — the question
is whether it's right for audio, where a digest tells you *that* something
finished but not *what*, which is the part worth hearing.

**Rough cost.** Medium — a day. Needs a debounce window, a rule for when to
digest versus read individually, and an answer for what happens to the
individual summaries afterwards (still in the queue for `repeat`, presumably).

**Risk.** Medium. A digest that fires when you wanted the detail is a strictly
worse outcome than the minute of talking, because the information is gone unless
you know to ask for it.

**Recommendation: Research.** Watch how often three-plus terminals actually
finish inside one window before designing around it.

---

## 8. Configurable wait thresholds

**What.** Make the 30s/5min green→yellow→red ladder settable.

**Why it matters.** It doesn't, yet. Nobody has asked. The numbers are guesses
that have held up, and a color ladder is a glanceable signal precisely because
it means the same thing in every terminal.

**Rough cost.** An hour.

**Risk.** The cost isn't the hour, it's the setting. Two more keys in a settings
surface that already carries nine, documented in three places (README, `/tts`,
the wizard), for a knob with no demonstrated demand.

**Recommendation: Kill until someone asks.**

---

## 9. Aging chime escalation

**What.** Re-chime, or chime more insistently, as a summary sits unread.

**Why it matters.** It doesn't. This was considered and rejected during the
build: a summary chimes once on arrival and then only changes color. Nagging is
worse than silence — the tool's whole posture is that it never takes your focus,
and a repeating tone is exactly a focus grab. The freshness rule that just
shipped moves in the *opposite* direction — a summary past red no longer reads
itself out on a click-in. (Not "stops chasing you entirely," as an earlier draft
of this said: the watcher arms on any *fresh* entry for that terminal and then
reads out everything it holds, stale included, and the red tab persists until
something clears it. The real gap, if one is worth closing, is at the far end:
after `WATCH_MAX_AGE_SECS` a summary is dropped from the queue having never been
surfaced by any cue.)

**Rough cost.** An hour.

**Risk.** It makes the tool annoying, which is the one thing that gets it
uninstalled.

**Recommendation: Kill.**

---

## 10. Other terminal emulators

**What.** Tab coloring for Ghostty, WezTerm, and Kitty. All three speak fine
today; none show color, because tinting is Terminal.app (AppleScript) and iTerm2
(OSC 6) only.

**Why it matters.** Color is half the product. On an unsupported emulator the
tool degrades to "a voice sometimes happens," which loses the at-a-glance wall of
terminals that makes multi-session work readable. Whether this is fixable at all
is an open question — it depends on each emulator exposing a tab-color escape
sequence, and there's no reason to assume they do.

**Rough cost.** Unknown, which is the point. Checking the three protocols is an
hour. Implementing could be a clean third branch in `_paint()`, or impossible.

**Risk.** Promising emulator support before confirming the escape sequences
exist is the risk. Also worth noting: the tint is only half of it — `focused_tty()`
asks each terminal app directly via AppleScript, and a non-scriptable emulator
breaks focus reads too, so "add a color branch" may not be the whole job.

**Recommendation: Research.** Cheap to answer, and the answer decides everything.

---

## 11. `/spoken-recap` per-entry tinting

**What.** Have `/spoken-recap` tint each terminal as its entry is read, instead
of tinting nothing.

**Why it matters.** Barely. One recap utterance can span several sessions, so
there's no single terminal to color — fixing it means splitting the recap into
per-entry playback with its own tint/untint lifecycle around each one.

**Rough cost.** Half a day, and it lands in the most delicate code in the
project: the tint record lifecycle, where a failed repaint strands a tab in a
color and the current design goes to real lengths (`_read_restore`, retry loop,
`restore_stale`, `--repair`) to make that unreachable.

**Risk.** High relative to the payoff. Real complexity in the one subsystem
whose failure mode is visible and persistent.

**Recommendation: Kill.**

---

## 12. Homebrew tap / `curl | sh` install

**What.** A `brew install` path, or a one-line curl installer, replacing
clone-and-run-`install.sh`.

**Why it matters.** It doesn't yet — for exactly one reason: nobody outside this
machine has installed it. `install.sh` is 102 lines and works. A tap is
distribution infrastructure for a distribution problem that hasn't appeared, and
it comes with an ongoing maintenance obligation (formula updates, version bumps)
that outlives the enthusiasm for setting it up.

**Rough cost.** Half a day for a tap, an hour for `curl | sh` — plus permanent
upkeep.

**Risk.** Low technically. The risk is spending the effort before knowing whether
anyone wants it, and `curl | sh` for a tool that installs shell hooks and
compiles two binaries is a trust ask this project hasn't earned yet.

**Recommendation: Defer** until someone else installs it. Revisit the moment a
second person does.

---

## Summary

| # | Idea | Call |
|---|---|---|
| 1 | Test suite + `CLAUDE_DIR` override | **Build first** |
| 2 | Demo GIF in README | **Build** |
| 3 | iPhone reach | **Research first** |
| 4 | Per-project voices | **Build** |
| 5 | CI | **Build after #1** |
| 6 | `repeat 2` / N-back | **Kill unless asked** |
| 7 | Digest roll-up | **Research** |
| 8 | Configurable wait thresholds | **Kill until asked** |
| 9 | Aging chime escalation | **Kill** |
| 10 | Other emulators | **Research** |
| 11 | `/spoken-recap` per-entry tinting | **Kill** |
| 12 | Homebrew tap / `curl \| sh` | **Defer** |
