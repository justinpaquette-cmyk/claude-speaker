# claude-speaker 🔊

**Your Claude Code terminals tell you when they're done — out loud, on-device, for free.**

No API keys, no cloud TTS, no tokens. Built on Claude Code's hooks and the Mac's built-in `say`.

```
you: (working in Chrome)
  🔊 "the docs terminal: Playwright is green, all 34 tests pass."
  🔊 "retool bot: stopped — the migration needs a column you haven't created yet."
```

## Why

Run four Claude Code sessions and you stop watching all four. You tab around hunting for the one that finished, and the one that got stuck sits there for ten minutes.

Speech turns *"go check every terminal"* into *"I'll hear it when something finishes."* The Apple system voice runs entirely on-device, so it costs nothing and works offline.

Then the problem becomes noise — four sessions all talking at once, or a voice cutting in while you're mid-thought. Most of this tool is the answer to that:

- **One voice at a time.** Ever. Others queue.
- **Nothing is lost.** A summary that couldn't be spoken waits, colors its terminal, and reads out when you go to it.
- **It shuts up when you're on a call.** Mic or camera live → silence, not even a chime.
- **`hold` mode** — nothing speaks unprompted at all. It chimes, colors the tab, and waits for you.

## Install

```bash
git clone https://github.com/justinpaquette-cmyk/claude-speaker.git && cd claude-speaker && ./install.sh
```

Restart your Claude Code sessions (or open `/hooks` once in each running one). The next response you get will speak.

The installer is idempotent and merge-safe. It copies three Python scripts to `~/.claude/scripts/` and three slash commands to `~/.claude/commands/`, compiles two small helpers (mic/camera detector, menu-bar indicator), adds a `Stop` and a `UserPromptSubmit` hook to your existing `~/.claude/settings.json` without touching anything else, and appends one convention to `~/.claude/CLAUDE.md` — end responses with a `🔊 one-liner` for the voice to read.

New here? Run **`/tts-wizard`**: it shows every feature with your current setting beside it and lets you pick a setup from menus.

## The terminal tells you what it wants

Colors carry the state, so a wall of terminals is readable at a glance. Nothing moves, nothing takes your keyboard.

| Color | Meaning |
|---|---|
| 🔵 **blue** | reading out right now |
| 🟢 **green** | has a summary ready and waiting |
| 🟡 **yellow** | has been waiting over 30s |
| 🔴 **red** | has been waiting over 5min |

**Click into a waiting terminal and it reads out**, then goes back to its own color. It triggers on the *switch-in*, not on the state — a terminal you're already sitting in never starts talking at you because a summary landed. You're looking right at it.

Buried behind another app? The **menu bar** carries the same state — `🔊 the docs terminal` while speaking, `🟡 2 waiting` while held — and a summary that *couldn't* be spoken raises a **notification banner**. Neither can take focus.

## When things speak: `chime` · `follow` · `hold`

| Mode | Voice free | Voice busy |
|---|---|---|
| **`chime`** *(default)* | speaks now | chimes when the other finishes, then waits |
| **`follow`** | speaks now | queues and auto-reads next, in finish order |
| **`hold`** | **waits** | **waits** |

`hold` is for when a voice would break your concentration: nothing ever speaks on its own. Every summary chimes softly, colors its tab, and waits to be clicked into. `/tts chime off` drops even the tone and leaves color alone.

Set per terminal or for everything: `/tts collision <chime|follow|hold> [global]`.

Follow mode only auto-reads summaries queued by follow-mode sessions in the last 5 minutes — older backlog and anything held during a call waits for you rather than surprising you later.

## Missed it? `repeat` — free replay

Type **`repeat`** (or **`rr`**) and it plays again, costing nothing. A `UserPromptSubmit` hook recognizes the trigger, speaks it, and exits 2 — which makes Claude Code **erase the prompt and never call the model**. No turn, no tokens, no context growth, the same "free to look at" feel as `/context`.

| Type | What happens |
|---|---|
| `repeat` / `rr` | This session's most recent summary, again |
| `repeat full` | **The whole response**, not the one-line summary |
| `repeat inverse` | Whichever rendering you *didn't* get — full if you're in `summary` mode, summary if you're in `full` |
| `repeat all` | Every session's unplayed summaries |
| `repeat status` | Lists this session's queue, speaks nothing |
| `shh` / `hush` / `repeat stop` | Shut the voice up right now |
| `stop` | Same — but only while the voice is talking; otherwise it's your prompt |

Every `repeat …` form also works as `rr …`.

`full` and `inverse` are free for the same reason: the hook payload carries this session's transcript path, so the turn is re-rendered from disk with the same sanitizer that spoke it. Heard the summary, want the detail? You get it without a model call.

Only those exact strings are intercepted — *"repeat full coverage for the auth tests"* goes to Claude untouched. `again` is deliberately **not** a trigger: it far more often means "do that again" than "say that again". For the same reason nothing is bound to <kbd>Esc</kbd>, which already interrupts the agent — a key that sometimes cancels your work and sometimes only silences audio is a key you stop trusting.

## On a call? It stays quiet

If your **microphone or camera is in use** — Zoom, Teams, FaceTime, a Meet tab, a screen recording — nothing plays at all, not even the chime. The summary just waits. A chime already deferred behind another readout re-checks at the moment it would actually play, so a call starting *during* the wait still silences it.

Detection uses a tiny compiled helper (`scripts/av-status.c`) reading the same CoreAudio/CoreMediaIO signals behind the orange and green menu-bar dots, so it covers any app including browser-tab calls. It needs no mic or camera permission and never touches the devices. Not built (no Xcode Command Line Tools)? Speech simply always plays.

## Commands

| Command | What it does |
|---|---|
| `/tts-wizard` | **Guided tour** — every feature with your current setting, configured from menus |
| `/tts` | Report current settings |
| `/tts <off\|summary\|full>` | What gets spoken — nothing, the 🔊 line, or the whole response |
| `/tts collision <chime\|follow\|hold>` | When it speaks (table above) |
| `/tts focus <on\|off>` | Read out when you click into a waiting terminal |
| `/tts color <color\|off>` | Speaking tint — `red` `orange` `yellow` `green` `blue` `purple` or `#rrggbb` |
| `/tts chime <on\|off>` | The soft arrival tone |
| `/tts menubar <on\|off>` · `/tts notify <on\|off>` | Menu-bar indicator · notification banners |
| `/tts length <n>` | Flat character cap for summaries (default: adaptive) |
| `/tts raise <window\|off>` | Opt-in window raise (see caveat below) |
| `/tts name <spoken name>` | What the voice calls this project's terminals |
| `/spoken-recap [status\|all]` | Replay a queue, list it, or replay every session's |

Every setting takes `global` to change the default for all sessions instead of just this one.

**What the voice calls a terminal:** its session title — set one with Claude Code's `/rename`, auto titles work too — else the `/tts name` custom name, else the project folder name. Titles and folder names are humanized for speech (camelCase and dashes split into words).

## How it works

```
response finishes ──▶ Stop hook (speak-response.py)
                        │  reads the last assistant message from the transcript
                        │  prefers the 🔊-marked line, else sanitizes + caps
                        │  appends to ~/.claude/tts-queue.jsonl
                        ├─ mic/camera live ─▶ silence, tab waits green→yellow→red
                        ├─ hold mode ───────▶ chime, tab waits green→yellow→red
                        ├─ voice idle ──────▶ /usr/bin/say, tab blue for the readout
                        └─ voice busy ──────▶ deferred chime, tab waits
                                                └─ click the tab ─▶ it reads out
```

Everything is outside the model loop: no API calls, no tokens, no context impact beyond the single 🔊 line per response.

| Path | What lives there |
|---|---|
| `~/.claude/tts-state.json` | Global settings |
| `~/.claude/tts-sessions/<id>.json` | Per-session overrides (safe to delete anytime) |
| `~/.claude/tts-queue.jsonl` | Every summary, spoken or waiting (last 200) |
| `~/.claude/tts-tabcolor/<tty>.json` | Each tinted terminal's real color, for restoring |
| `~/.claude/tts-badge.txt` | What the menu-bar helper is currently showing |

Aging colors and click-to-read are handled by one locked watcher (`speak-response.py --watch`) that starts when something first has to wait and **exits as soon as the queue is clear** — nothing polls in the background during normal use. It asks the terminal app for its own frontmost tab, so it needs no Accessibility permission.

Colors are Terminal.app tab backgrounds (AppleScript, matched by tty) or iTerm2 tab colors (OSC 6). Any other emulator skips the tint and still speaks. A terminal's real color is parked on disk and a tint is dropped **only once the repaint is confirmed**, so no crash or kill can strand a terminal in a color — `speak-response.py --repair` is the last resort if one ever does.

### The window-raise caveat

`/tts raise window` brings the speaking window to the top, and is **off by default** for a reason worth knowing: macOS offers no raise that's guaranteed focus-free. `activate` steals focus across apps outright and is never used. `AXRaise` doesn't cross apps — but inside an app it makes the raised window *key*, so the next thing you type in that app can land there. It's skipped whenever your terminal app is already frontmost, so typing in a terminal is never interrupted. Want zero window movement, leave it off.

It also needs Accessibility permission (System Settings → Privacy & Security → Accessibility). Without it the raise silently does nothing; `speak-response.py --check-raise` tells you which state you're in.

## Tips

- The voice is whatever macOS is set to — the newer **Siri voices** sound far better than the default. System Settings → Accessibility → Spoken Content → System voice.
- The 🔊 convention lives in `~/.claude/CLAUDE.md`. Tweak the wording there to change how summaries sound.
- Summary length is adaptive by default: it scales with how much the turn actually produced, so a big multi-step turn earns a longer readout and a one-liner stays short.

## Requirements

macOS (uses `/usr/bin/say` and `afplay`), Claude Code, Python 3. Xcode Command Line Tools for the two optional compiled helpers — without them, call-detection and the menu-bar item are skipped and everything else works.

## Uninstall

```bash
rm ~/.claude/scripts/speak-response.py ~/.claude/scripts/tts-recap.py \
   ~/.claude/scripts/repeat-hook.py ~/.claude/scripts/av-status \
   ~/.claude/scripts/speaking-badge ~/.claude/tts-badge.txt \
   ~/.claude/commands/tts.md ~/.claude/commands/tts-wizard.md \
   ~/.claude/commands/spoken-recap.md \
   ~/.claude/tts-state.json ~/.claude/tts-queue.jsonl
rm -rf ~/.claude/tts-sessions ~/.claude/tts-tabcolor
```

Then remove the `Stop` and `UserPromptSubmit` entries from `~/.claude/settings.json` (or via `/hooks`), and the "Spoken Summary (TTS)" section from `~/.claude/CLAUDE.md`.

## License

MIT
