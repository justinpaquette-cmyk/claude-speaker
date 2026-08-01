# claude-speaker 🔊

Make Claude Code **speak its responses out loud** in your macOS terminal — free, on-device, no API keys, no cloud TTS. Built on Claude Code's Stop hook and the Mac's built-in `say` command.

You keep typing in the terminal exactly as before. When a response finishes, Claude reads you a one-sentence spoken summary. Built for running many sessions at once: only one voice ever speaks at a time, and nothing gets lost.

## Why

If you run several Claude Code sessions in parallel, you stop watching them all. Speech turns "go check every terminal" into "I'll hear it when something finishes" — at zero cost, since the Apple system voice runs entirely on-device.

## Install

```bash
git clone https://github.com/justinpaquette-cmyk/claude-speaker.git && cd claude-speaker && ./install.sh
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
| `/tts collision follow` | This session's summaries auto-read right after the current speech |
| `/tts collision chime [global]` | Back to chime-and-queue (default) |
| `/tts name <spoken name>` | Fallback spoken name for this project's terminals |
| `/tts color <color\|off>` | Tint shown while this terminal speaks (default `red`) |
| `/tts raise <window\|off>` | Opt-in: raise the speaking window (default `off`) |

**What the voice calls a terminal:** its session title — set one with Claude Code's built-in `/rename` (auto titles work too) — else the `/tts name` custom name, else the project folder name. Titles and folder names are humanized for speech (camelCase/dashes split into words).
| `rr` | Replay the last summary — **no model turn, no tokens** (see below) |
| `/spoken-recap` | Replay this session's queued summaries |
| `/spoken-recap status` | List this session's queue without speaking |
| `/spoken-recap all` | Replay queued summaries from every session |

## Missed it? `rr` — free replay

Type **`rr`** and the last summary plays again. It costs nothing: a `UserPromptSubmit` hook recognizes the trigger, speaks the summary itself, and exits 2, which makes Claude Code **erase the prompt and never call the model**. No turn, no tokens, no context growth — the same "free to look at" feel as `/context`.

| Type | What happens |
|---|---|
| `rr` | This session's most recent summary, again |
| `rr all` | Every session's unplayed summaries |
| `rr status` | Lists this session's queue, speaks nothing |

Only those three exact strings are intercepted; anything else goes to Claude untouched. If a summary is mid-readout, `rr` says so rather than talking over it.

## See which session is talking

While a summary is being read, that terminal **turns red** — and goes right back to normal when the voice stops. With several sessions running you can look up mid-sentence and know which one is speaking. Nothing moves, nothing takes your keyboard.

- **Tint** — Terminal.app tab background (AppleScript, matched by tty) or iTerm2 tab color (OSC 6). Any other emulator just skips the tint and still speaks. `/tts color <red|orange|yellow|green|blue|purple|#rrggbb|off>`.
- **Raise** (`/tts raise window`, **off by default**) — brings the speaking window to the top via System Events `AXRaise`; `activate` is never used. It stays opt-in because macOS offers no raise that is guaranteed focus-free: AXRaise doesn't cross apps, but inside an app the raised window becomes *key*, so the next thing you type in that app can land there. It is skipped whenever your terminal app is frontmost, so typing in a terminal is never interrupted. Want zero window movement? Leave it off.
- Raising needs **Accessibility** permission for your terminal app (System Settings → Privacy & Security → Accessibility). Without it the raise silently does nothing; `python3 ~/.claude/scripts/speak-response.py --check-raise` tells you which state you're in.
- The pre-speech color is parked in `~/.claude/tts-tabcolor/<tty>.json`, so a killed watcher can't leave a terminal stuck red — the next hook run puts it back.

## How multi-session collisions work

- Voice idle → a finishing session **speaks immediately**.
- Voice busy → the **collision setting** decides, per session (override) or globally:
  - **`chime`** (default) — wait for the current speech to end, play a soft **chime**, and queue the summary for `/spoken-recap`. You set the pacing.
  - **`follow`** — queue the summary and **speak it automatically right after** the current speech (and any earlier queued summaries) finishes, prefixed with its project name. Sessions read out in finish order, serialized — never over each other.
- `/tts collision <chime|follow> [global]` switches between them; `/spoken-recap` in any terminal replays a session's queued summaries either way.

Follow mode only auto-reads summaries queued by follow-mode sessions in the last 5 minutes — older backlog and summaries held during a call never replay on their own; they wait for `/spoken-recap`.

## On a call? It stays quiet

If your **microphone or camera is actively in use** — a Zoom/Teams/FaceTime call, a Meet tab, a screen recording — the hook says nothing at all (not even the chime) and just queues the summary. Run `/spoken-recap` after the call to hear what you missed.

Detection uses a tiny compiled helper (`scripts/av-status.c`, built by the installer) that reads the same CoreAudio/CoreMediaIO signals behind the orange and green menu-bar dots, so it works for any app, including browser-tab calls. It needs no mic/camera permissions and never touches the devices itself. If the helper isn't built (no Xcode Command Line Tools), speech simply always plays.

## How it works

```
response finishes ──▶ Stop hook (speak-response.py)
                        │  reads last assistant message from the transcript
                        │  prefers the 🔊-marked summary line, else sanitizes + caps
                        │  logs to ~/.claude/tts-queue.jsonl
                        ├─ mic/camera in use ──▶ full silence + queue for /spoken-recap
                        ├─ voice idle ──▶ /usr/bin/say  (on-device Apple TTS)
                        │                  + tab turns red for the readout, restored after
                        └─ voice busy ──▶ deferred chime + queue for /spoken-recap
```

State lives in `~/.claude/tts-state.json` (global mode) and `~/.claude/tts-sessions/<session-id>.json` (per-session overrides). The hook is fully outside the model loop: no API calls, no tokens, no context impact beyond the one 🔊 line per response.

## Tips

- The voice is whatever macOS is set to — the newer **Siri voices** sound far better than the default. System Settings → Accessibility → Spoken Content → System voice.
- The 🔊 convention lives in `~/.claude/CLAUDE.md`; tweak the wording there to change how summaries sound.

## Uninstall

```bash
rm ~/.claude/scripts/speak-response.py ~/.claude/scripts/tts-recap.py \
   ~/.claude/scripts/repeat-hook.py ~/.claude/scripts/av-status \
   ~/.claude/commands/tts.md ~/.claude/commands/spoken-recap.md \
   ~/.claude/tts-state.json ~/.claude/tts-queue.jsonl
rm -rf ~/.claude/tts-sessions ~/.claude/tts-tabcolor
```

Then remove the `Stop` and `UserPromptSubmit` hook entries from `~/.claude/settings.json` (or via `/hooks`) and the "Spoken Summary (TTS)" section from `~/.claude/CLAUDE.md`.

## Requirements

macOS (any recent version — uses `/usr/bin/say` and `afplay`), Claude Code, Python 3 (ships with Xcode Command Line Tools, which Claude Code users already have).
