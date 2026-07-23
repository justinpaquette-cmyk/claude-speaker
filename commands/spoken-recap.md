# /spoken-recap — Replay queued spoken summaries (this session)

When multiple sessions finish while one is already talking, their summaries queue up (you hear a deferred chime instead of a collision). This replays THIS session's queued summaries so you control the pacing terminal-by-terminal.

## Usage

- `/spoken-recap` — speak this session's unplayed summaries oldest-first
- `/spoken-recap latest` — re-speak this session's most recent summary (played or not)
- `/spoken-recap status` — list this session's queue, speak nothing
- `/spoken-recap all [latest|status]` — same, but across every session

## Steps

1. Unless `all` was given, identify THIS session's ID — the basename (without `.jsonl`) of the most recently modified transcript in this project's dir, reliable mid-turn because this session just wrote the user's prompt to it:
   ```bash
   ls -t ~/.claude/projects/<encoded-cwd>/*.jsonl | head -1
   ```
   (`<encoded-cwd>` = cwd with `/` and spaces → `-`.)
2. Run `python3 ~/.claude/scripts/tts-recap.py` with:
   - `--session <id>` unless `all` was given
   - `--latest` for `latest`, `--status` for `status`
3. Relay the script's stdout to the user verbatim (it lists what was spoken/queued). Do not add a 🔊 line to your reply — the recap itself is the speech, and a marker line would immediately queue behind it.
