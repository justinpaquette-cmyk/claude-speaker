# /tts — Toggle spoken responses

Control the Stop-hook TTS (`~/.claude/scripts/speak-response.py`). Two settings:

**Mode** — what gets spoken:
- `off` — silent
- `summary` — speak only the final 🔊 line, trimmed to the summary length (default)
- `full` — speak the entire sanitized response (uncapped)

**Length** — summary character cap (`summary` mode only; `full` is uncapped):
- **Default is adaptive**: proportional to the turn's sanitized output volume
  (`chars × 0.5`, clamped to `400–2500`) — a big turn earns a longer spoken
  summary, a one-liner stays short.
- `/tts length <n>` overrides the formula with a flat cap (positive integer).
  The 🔊 line (or fallback) is trimmed at a word boundary with a spoken
  "full response is in the terminal" tail.

**Color** — the tab color says what the terminal wants:
- **blue** — reading out right now
- **purple** — an AskUserQuestion is open: it is blocked on *your* answer
- **green** — has a summary ready and waiting
- **yellow** — waiting over 30s · **red** — waiting over 5min
- `/tts color <color|off>` sets the *speaking* color (default `blue`); the
  waiting ladder is fixed. `off` disables tinting for this terminal entirely.

**Focus** — `focus_speak` (default `on`): **being at** a waiting terminal makes
it read out and clear its color — either by clicking into it, or by a summary
landing in the tab you are already watching (in `hold`, that is the one thing
that speaks unprompted). A click-in reads out whatever the terminal holds, at
any age — a colored tab is one you have not heard yet.
`/tts focus off` leaves everything waiting for a replay word (`rr`, `repeat`,
`replay`, `recap` or `tts` — all the same command) or `/spoken-recap`. The
queue is a stack, so a replay word also takes a count or a window: `rr 3`
pops the last three updates, `rr 5m` pops the last five minutes.

**Ask** — the purple "you are the blocker" cue, the only one that means a session
is stopped waiting on you rather than holding something for you (off the
green→yellow→red ladder on purpose: a different kind of state, not a later stage
of waiting):
- Shown **only while that tab is in the background**. The question is already on
  screen in the tab you're looking at, so tinting it would be noise — switch away
  and it goes purple, switch back and the tint drops. The question stays open
  either way; this is a cue, not a state machine.
- It **outranks** the waiting ladder on the same tab: a live question beats an
  old summary.
- Static — a decision doesn't get more urgent by being ignored, it just stays
  yours. No aging to red.
- `ask_speak` (default `on`) reads the question's *headers* aloud when one opens
  unfocused ("waiting on your call. Aging, Audio") so you know what's being asked
  without going to look. Silent on a call, silent if the voice is already busy,
  and **silent under `hold`** — `hold` means nothing speaks at a terminal you
  aren't watching, and a question is the loudest kind of unprompted speech there
  is, not an exception to it. The purple tab and the menu bar carry it instead.
- `/tts ask <color|off>` changes or kills the tint; `/tts ask-speak off` kills the
  speech in `chime` and `follow` too.

**Menu bar** — `menubar` (default `on`): the top bar shows `🔊 <terminal>` while
something is speaking, or `🟡 2 waiting` when summaries are held. Needs the
`speaking-badge` helper the installer builds.

**Notify** — `notify` (default `on`): a banner when a summary *cannot* be spoken
(you were on a call, or another terminal had the voice). Never for one you just
heard.

**Raise** — whether the speaking terminal's window comes to the top:
- `off` (default) — nothing ever moves; the tint is the only cue.
- `window` — opt-in raise, skipped while the terminal app is frontmost. Even
  then macOS makes the raised window *key* inside its own app, so the next
  thing you type in that app can land there. Needs Accessibility permission
  (Terminal.app or iTerm2 only).

**Delivery** (the `collision` setting) — when a finished summary gets spoken:
- `chime` (default) — speak now if the voice is free; if another session is
  talking, chime once that speech ends and leave this one waiting
- `follow` — same, but a waiting summary auto-reads right after the current one
- `hold` — **never speak at a terminal you're not watching**, even with the voice
  free: chime, color the tab, and wait to be clicked into. A summary landing in
  the focused tab still reads out — you're looking right at it. Deep-work mode.

**Chime** — `chime` (default `on`): the soft arrival tone. `/tts chime off` makes
waiting summaries completely silent, color only.

Scope: **this session** by default; add `global` to set the default for all sessions.

## Usage

`/tts <off|summary|full> [global]` — set the mode.
`/tts length <n> [global]` — set the summary character cap (e.g. `/tts length 2000`).
`/tts collision <chime|follow|hold> [global]` — set the delivery policy.
`/tts chime <on|off> [global]` — the arrival tone.
`/tts color <off|red|orange|yellow|green|blue|purple|#rrggbb> [global]` — speaking tint.
`/tts ask <off|red|orange|yellow|green|blue|purple|#rrggbb> [global]` — tint for a terminal holding an open AskUserQuestion (default purple; shown only while that tab is in the background).
`/tts ask-speak <on|off> [global]` — read the question's headers aloud when one opens unfocused (`hold` silences it regardless).
`/tts focus <on|off> [global]` — read out when you click into a waiting terminal.
`/tts menubar <on|off> [global]` — menu-bar indicator.
`/tts notify <on|off> [global]` — banner for summaries that had to wait.
`/tts raise <window|off> [global]` — raise the speaking window (no focus steal).
`/tts name <spoken name>` — how the voice announces THIS project (e.g. `/tts name the docs terminal`).
No argument = report current state.
`/tts-wizard` — guided tour of every feature with pick-from-a-list configuration.

## Steps

Do not edit the settings files by hand — the script owns them, validates values,
and merges without clobbering:

```bash
python3 ~/.claude/scripts/speak-response.py --settings [--session <id>]
python3 ~/.claude/scripts/speak-response.py --set <key> <value> [--session <id>]
```

1. Map the first word to a setting key: `collision`→`collision`, `chime`→`chime`,
   `length`→`summary_chars`, `color`→`tab_color`, `ask`→`ask_color`,
   `ask-speak`→`ask_speak`, `focus`→`focus_speak`,
   `menubar`→`menubar`, `notify`→`notify`, `raise`→`raise`; anything else that is
   a valid mode (`off`/`summary`/`full`) means `mode`. No valid argument → skip to
   step 4 (report only). `name` is special — see below.

   **`name`:** merge into `~/.claude/tts-state.json` (always global) a `"names"` map entry keyed by the basename of the current working directory, value = the given spoken name, e.g. `{"names": {"retoolBot": "the retool terminal"}}`. Read-modify-write, preserving all other keys. Then report and stop.

   Note the announce-name precedence: the session's title (Claude Code's `/rename`, or its auto title) wins over this map, which wins over the humanized folder name. To rename ONE terminal, `/rename` is usually what you want; `/tts name` sets the fallback for all terminals in this project folder.

2. **Global scope** (`global` present): call `--set` with no `--session`.

3. **Session scope** (default): identify THIS session's ID — the basename (without `.jsonl`) of the most recently modified transcript in this project's dir, which is reliable mid-turn because this session just wrote the user's prompt to it:
   ```bash
   ls -t ~/.claude/projects/<encoded-cwd>/*.jsonl | head -1
   ```
   (`<encoded-cwd>` = cwd with `/` and spaces → `-`.) Pass it as `--session <id>`.

   Echo what `--set` prints rather than claiming success yourself — it reports the
   key, the value and the scope it wrote, and it refuses invalid values.

   **After setting `raise window`,** also run `--check-raise` and report its line:
   raising silently does nothing until Accessibility is granted to the terminal app.

4. Report state by running `--settings --session <id>` and summarizing it in a line
   or two (it prints every key, its value, and whether that came from this session,
   global, or the default).

5. If mode is `off` for this session, stop ending responses with the 🔊 line until it's turned back on. If `full`, the 🔊 line is unnecessary — drop it (the hook strips it anyway).

Notes: per-session override files are keyed by session ID and go stale harmlessly after the session ends; safe to delete `~/.claude/tts-sessions/*` anytime (`.seen` files there are the hook's freshness fingerprints — also safe to delete).
