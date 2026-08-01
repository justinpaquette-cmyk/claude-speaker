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
- **green** — has a summary ready and waiting
- **yellow** — waiting over 30s · **red** — waiting over 5min
- `/tts color <color|off>` sets the *speaking* color (default `blue`); the
  waiting ladder is fixed. `off` disables tinting for this terminal entirely.

**Focus** — `focus_speak` (default `on`): focusing a waiting terminal makes it
read out on the spot and clear its color. `/tts focus off` leaves it waiting for
`rr` or `/spoken-recap` instead.

**Raise** — whether the speaking terminal's window comes to the top:
- `off` (default) — nothing ever moves; the tint is the only cue.
- `window` — opt-in raise, skipped while the terminal app is frontmost. Even
  then macOS makes the raised window *key* inside its own app, so the next
  thing you type in that app can land there. Needs Accessibility permission
  (Terminal.app or iTerm2 only).

**Collision** — what happens when another session's voice is already talking:
- `chime` — soft chime after the current speech; summary queues for `/spoken-recap` (default)
- `follow` — summary auto-reads right after the current speech ends

Scope: **this session** by default; add `global` to set the default for all sessions.

## Usage

`/tts <off|summary|full> [global]` — set the mode.
`/tts length <n> [global]` — set the summary character cap (e.g. `/tts length 2000`).
`/tts collision <chime|follow> [global]` — set the collision policy.
`/tts color <off|red|orange|yellow|green|blue|purple|#rrggbb> [global]` — speaking tint.
`/tts focus <on|off> [global]` — read out when the waiting terminal is focused.
`/tts raise <window|off> [global]` — raise the speaking window (no focus steal).
`/tts name <spoken name>` — how the voice announces THIS project (e.g. `/tts name the docs terminal`).
No argument = report current state.

## Steps

1. Parse the arguments. `collision` as the first word means the setting is `collision` (valid values: `chime`, `follow`); `length` as the first word means the setting is `summary_chars` (value = the following positive integer); `color` as the first word means the setting is `tab_color` (value = the following color word, `#rrggbb`, or `off`); `focus` as the first word means the setting is `focus_speak` (valid values: `on`, `off`); `raise` as the first word means the setting is `raise` (valid values: `window`, `off`); `name` as the first word means everything after it is the spoken name for this project; otherwise the setting is `mode` (valid values: `off`, `summary`, `full`). If no valid argument, skip to step 4 (report only).

   **`raise`:** after writing `window`, run `python3 ~/.claude/scripts/speak-response.py --check-raise` and report its line — raising silently does nothing until Accessibility is granted to the terminal app.

   **`length`:** write the integer under the key `"summary_chars"` (not `"length"`) into the target file per the scope rules below (session by default, `global` for all sessions). Read-modify-write, preserving other keys.

   **`name`:** merge into `~/.claude/tts-state.json` (always global) a `"names"` map entry keyed by the basename of the current working directory, value = the given spoken name, e.g. `{"names": {"retoolBot": "the retool terminal"}}`. Read-modify-write, preserving all other keys. Then report and stop.

   Note the announce-name precedence: the session's title (Claude Code's `/rename`, or its auto title) wins over this map, which wins over the humanized folder name. To rename ONE terminal, `/rename` is usually what you want; `/tts name` sets the fallback for all terminals in this project folder.

2. **Global scope** (`global` present): target file is `~/.claude/tts-state.json`.

3. **Session scope** (default): identify THIS session's ID — it is the basename (without `.jsonl`) of the most recently modified transcript in this project's dir, which is reliable mid-turn because this session just wrote the user's prompt to it:
   ```bash
   ls -t ~/.claude/projects/<encoded-cwd>/*.jsonl | head -1
   ```
   (`<encoded-cwd>` = cwd with `/` and spaces → `-`.) Then `mkdir -p ~/.claude/tts-sessions`; target file is `~/.claude/tts-sessions/<session_id>.json`.

   **Write by merging, never clobbering:** read the target file's existing JSON (absent/invalid = `{}`), set just the one key (`"mode"`, `"collision"`, `"tab_color"`, `"raise"`, …), write it back. All of these keys live in the same files.

4. Report state: from `~/.claude/tts-state.json` the global `mode` (absent = `summary`), `collision` (absent = `chime`), `summary_chars` (absent = `adaptive`), `tab_color` (absent = `red`), and `raise` (absent = `window`), plus this session's overrides from `~/.claude/tts-sessions/<session_id>.json` (absent = follows global), e.g. "TTS: this session = full (override), global default = summary, length = adaptive, collision = follow (global), color = red, raise = window".

5. If mode is `off` for this session, stop ending responses with the 🔊 line until it's turned back on. If `full`, the 🔊 line is unnecessary — drop it (the hook strips it anyway).

Notes: per-session override files are keyed by session ID and go stale harmlessly after the session ends; safe to delete `~/.claude/tts-sessions/*` anytime (`.seen` files there are the hook's freshness fingerprints — also safe to delete).
