# claude-speaker 🔊

Make Claude Code **speak its responses out loud** in your macOS terminal — free, on-device, no API keys, no cloud TTS. Built on Claude Code's Stop hook and the Mac's built-in `say` command.

You keep typing in the terminal exactly as before. When a response finishes, Claude reads you a one-sentence spoken summary. Built for running many sessions at once: only one voice ever speaks at a time, and nothing gets lost.

## Why

If you run several Claude Code sessions in parallel, you stop watching them all. Speech turns "go check every terminal" into "I'll hear it when something finishes" — at zero cost, since the Apple system voice runs entirely on-device.

## Install

```bash
git clone <this-repo-url> && cd claude-speaker && ./install.sh
```

Then restart your Claude Code sessions (or open `/hooks` once in each running one). That's it — the next response you get will end with a spoken summary.

The installer is idempotent and merge-safe: it copies two Python scripts into `~/.claude/scripts/`, two slash commands into `~/.claude/commands/`, appends a Stop hook to your existing `~/.claude/settings.json` (never overwriting other settings), and appends a short convention to your `~/.claude/CLAUDE.md` telling Claude to end responses with a `🔊 one-liner` for the voice to read.

## Usage

Nothing to do — responses just speak. Control it with two slash commands:

| Command | What it does |
|---|---|
| `/tts off` | Silence **this session** (others keep talking) |
| `/tts summary` | Default: speak only the final 🔊 summary line |
| `/tts full` | Speak the entire response (markdown/code sanitized out) |
| `/tts <mode> global` | Set the default for all sessions |
| `/spoken-recap` | Replay this session's queued summaries |
| `/spoken-recap status` | List this session's queue without speaking |
| `/spoken-recap all` | Replay queued summaries from every session |

## How multi-session collisions work

- Voice idle → a finishing session **speaks immediately**.
- Voice busy → the session waits for the current speech to end, plays a soft **chime**, and queues its summary.
- `/spoken-recap` in any terminal replays that session's queued summaries, each prefixed with its project name — you set the pacing.

## How it works

```
response finishes ──▶ Stop hook (speak-response.py)
                        │  reads last assistant message from the transcript
                        │  prefers the 🔊-marked summary line, else sanitizes + caps
                        │  logs to ~/.claude/tts-queue.jsonl
                        ├─ voice idle ──▶ /usr/bin/say  (on-device Apple TTS)
                        └─ voice busy ──▶ deferred chime + queue for /spoken-recap
```

State lives in `~/.claude/tts-state.json` (global mode) and `~/.claude/tts-sessions/<session-id>.json` (per-session overrides). The hook is fully outside the model loop: no API calls, no tokens, no context impact beyond the one 🔊 line per response.

## Tips

- The voice is whatever macOS is set to — the newer **Siri voices** sound far better than the default. System Settings → Accessibility → Spoken Content → System voice.
- The 🔊 convention lives in `~/.claude/CLAUDE.md`; tweak the wording there to change how summaries sound.

## Uninstall

```bash
rm ~/.claude/scripts/speak-response.py ~/.claude/scripts/tts-recap.py \
   ~/.claude/commands/tts.md ~/.claude/commands/spoken-recap.md \
   ~/.claude/tts-state.json ~/.claude/tts-queue.jsonl
rm -rf ~/.claude/tts-sessions
```

Then remove the `Stop` hook entry from `~/.claude/settings.json` (or via `/hooks`) and the "Spoken Summary (TTS)" section from `~/.claude/CLAUDE.md`.

## Requirements

macOS (any recent version — uses `/usr/bin/say` and `afplay`), Claude Code, Python 3 (ships with Xcode Command Line Tools, which Claude Code users already have).
