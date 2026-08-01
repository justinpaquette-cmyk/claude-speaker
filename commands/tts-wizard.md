# /tts-wizard — tour the spoken-summary features and pick your setup

An interactive refresher: show what claude-speaker can do, show what's currently
on, and let the user change it by picking from options rather than remembering
flag names. Use this when they ask "what can this do again?", want to reconfigure,
or type `/tts-wizard`.

Never write the settings files by hand — `speak-response.py` has a settings API
that validates values and merges safely:

```bash
python3 ~/.claude/scripts/speak-response.py --settings [--session <id>]   # read
python3 ~/.claude/scripts/speak-response.py --set <key> <value> [--session <id>]
```

Session ID (only needed for session scope) — the newest transcript in this
project's dir, which this session just wrote to:

```bash
ls -t ~/.claude/projects/<encoded-cwd>/*.jsonl | head -1   # cwd, / and spaces → -
```

## Steps

1. **Read the current setup** with `--settings --session <id>`. Do this first —
   the tour should say what is already on, not describe the tool in the abstract.

2. **Give the tour**, compactly — a short table, not paragraphs. Mark each line
   with the user's current value so it reads as "here's you" rather than a manual:

   | Feature | What it does |
   |---|---|
   | **Delivery** (`collision`) | `chime` speak now if the voice is free, else chime and wait · `follow` waiting summaries auto-read in finish order · `hold` nothing speaks at a terminal you're not watching — chime, color the tab, wait to be clicked into; a summary landing in the focused tab still reads out |
   | **Click-to-talk** (`focus_speak`) | Being at a waiting terminal reads it out — clicking in, or a summary arriving in the tab you're already watching. Any age: a colored tab is one you haven't heard |
   | **Colors** (`tab_color`) | 🔵 speaking · 🟢 waiting · 🟡 over 30s · 🔴 over 5min. The speaking color is settable; the waiting ladder is fixed. `off` disables tinting |
   | **Question cue** (`ask_color`, `ask_speak`) | 🟣 an AskUserQuestion is open — the one color that means *you* are the blocker. Background tabs only (the tab you're in already shows the question), outranks the waiting ladder, never ages. `ask_speak` reads the question's headers aloud when it opens unfocused — silent under `hold`, which means nothing speaks at a terminal you aren't watching |
   | **Menu bar** (`menubar`) | Top bar shows `🔊 <terminal>` speaking or `🟡 2 waiting` |
   | **Banners** (`notify`) | Notification for a summary that *couldn't* be spoken. Never for one you just heard |
   | **Chime** (`chime`) | The soft arrival tone. Off = color only |
   | **Mode** (`mode`) | `summary` speaks the 🔊 line · `full` speaks the whole response · `off` silent |
   | **Length** (`summary_chars`) | Adaptive by default (scales with how big the turn was); a number sets a flat cap |
   | **Raise** (`raise`) | Off by default — macOS can't raise a window without making it key inside its app, so it can take your keyboard |

   Also mention the two free tricks, since they cost nothing and people forget:
   **`rr`** replays the last summary with no model turn and no tokens — and
   so do **`repeat`**, **`replay`**, **`recap`** and **`tts`**, all five being
   the same command so you never have to recall which word it was (each also
   takes ` all` / ` full` / ` inverse` / ` status` / ` stop`);
   **`/spoken-recap`** replays a whole queue.

3. **Ask, don't quiz.** One `AskUserQuestion` call with these four:
   - *Scope* — "this session only" vs "all sessions (global)". Put whichever
     matches their phrasing first.
   - *Delivery* — hold / chime / follow, described in plain terms ("never
     interrupt me" vs "speak when free" vs "queue up and read in order").
   - *Cues* — **multiSelect**: tab colors, purple question cue, menu bar, banners,
     arrival chime.
     Pre-frame it as "which of these do you want on" and treat unselected ones
     as off.
   - *Click-to-talk* — on/off.

   Skip any question the user has already answered in their message. If they
   only asked to change one thing, just change it and confirm — do not run the
   whole wizard at them.

4. **Apply** each choice with a `--set` call (session or global per the scope
   answer). Cue answers map to: `tab_color` (a color name or `off`), `ask_color`
   (a color name or `off`; `ask_speak` on/off rides with it), `menubar`,
   `notify`, `chime` — each `on`/`off`.

5. **Confirm** by re-running `--settings` and showing the result, then say in one
   line what will actually happen next time a response finishes under the new
   setup (e.g. "next summary will chime, turn this tab green, and wait until you
   click in"). If they chose `hold`, warn that summaries will now be silent until
   clicked — that surprise is the whole point of saying it out loud.

Notes: settings are per-session unless global; a session file only overrides the
keys it contains. `--set` validates and reports what it wrote, so echo its output
rather than claiming success on your own.
