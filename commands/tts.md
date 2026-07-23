# /tts — Toggle spoken responses

Control the Stop-hook TTS (`~/.claude/scripts/speak-response.py`). Modes:
- `off` — silent
- `summary` — speak only the final 🔊 line (default)
- `full` — speak the entire sanitized response

Scope: **this session** by default; add `global` to set the default for all sessions.

## Usage

`/tts <off|summary|full> [global]` — no argument = report current state.

## Steps

1. Parse the arguments. Valid modes: `off`, `summary`, `full`. If no mode given, skip to step 4 (report only).

2. **Global scope** (`global` present): write `{"mode": "<mode>"}` to `~/.claude/tts-state.json`.

3. **Session scope** (default): identify THIS session's ID — it is the basename (without `.jsonl`) of the most recently modified transcript in this project's dir, which is reliable mid-turn because this session just wrote the user's prompt to it:
   ```bash
   ls -t ~/.claude/projects/<encoded-cwd>/*.jsonl | head -1
   ```
   (`<encoded-cwd>` = cwd with `/` and spaces → `-`.) Then `mkdir -p ~/.claude/tts-sessions` and write `{"mode": "<mode>"}` to `~/.claude/tts-sessions/<session_id>.json`.

4. Report state: global mode from `~/.claude/tts-state.json` (absent = `summary`), this session's override from `~/.claude/tts-sessions/<session_id>.json` (absent = follows global), e.g. "TTS: this session = full (override), global default = summary".

5. If mode is `off` for this session, stop ending responses with the 🔊 line until it's turned back on. If `full`, the 🔊 line is unnecessary — drop it (the hook strips it anyway).

Notes: per-session override files are keyed by session ID and go stale harmlessly after the session ends; safe to delete `~/.claude/tts-sessions/*` anytime.
