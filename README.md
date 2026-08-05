# claude-speaker 🔊

**Claude Code speaks its results out loud.** Free, on-device, no API keys, no cloud TTS, no tokens.

```
🔊 "the docs terminal: Playwright is green, all 34 tests pass."
🔊 "retool bot: stopped — the migration needs a column you haven't created yet."
```

## Install in 30 seconds

```bash
git clone https://github.com/justinpaquette-cmyk/claude-speaker.git && cd claude-speaker && ./install.sh
```

Restart your Claude Code sessions and you're done — the next response speaks. One command, no config, no account, nothing to sign up for. Run **`/tts-wizard`** any time to see every feature with your current setting beside it and change it from a menu.

*(The installer is idempotent and merge-safe: three scripts into `~/.claude/scripts/`, three slash commands into `~/.claude/commands/`, two small compiled helpers, and four hooks added to your existing `settings.json` without touching anything else. An installed file you'd edited by hand is backed up to `<name>.bak-<timestamp>` rather than overwritten.)*

## What you get

- 🗣️ **Spoken summaries** the moment a response finishes, in the macOS voice you already like.
- 🎨 **Terminals that show their state in color** — 🔵 speaking · 🟣 asking you something · 🟢 waiting · 🟡 30s · 🔴 5min. A wall of terminals becomes readable at a glance.
- 🟣 **The one color that means *you're* the blocker.** A session stuck on a question goes purple — only while that tab is in the background, because the tab you're in already shows it.
- 👆 **Click a waiting terminal and it reads out.** Whatever it's holding, however long it's been holding it.
- 🔁 **`repeat` replays anything — for free.** No model turn, no tokens, no context. `repeat full` gives you the entire response instead of the one-liner, also free.
- 🤫 **`shh`** stops the voice mid-sentence.
- 🎧 **Silent on calls.** Mic or camera live and it says nothing at all, chime included.
- 🧘 **`hold` mode** — nothing speaks at a terminal you're not watching. It chimes, colors the tab, and waits until you're ready. Land in the tab it happened in and it just tells you.
- 📊 **Menu bar and notifications** carry the same state when every terminal is buried. Neither can take your focus.
- 🔀 **Built for many sessions at once** — one voice at a time, ever, and nothing is ever lost or talked over.

## The terminal tells you what it wants

Colors carry the state, so a wall of terminals is readable at a glance. Nothing moves, nothing takes your keyboard.

| Color | Meaning |
|---|---|
| 🔵 **blue** | reading out right now |
| 🟣 **purple** | a question is open — it's blocked on *your* answer |
| 🟢 **green** | has a summary ready and waiting |
| 🟡 **yellow** | has been waiting over 30s |
| 🔴 **red** | has been waiting over 5min |

**Click into a waiting terminal and it reads out**, then goes back to its own color — whatever age the summary is. A colored tab is one you haven't heard yet, so clicking it always clears it. And **the terminal you're already in counts as focused**: a summary landing in the tab you're watching just tells you, instead of making you click away and back to hear it.

It reads the **newest 3**, not the whole backlog. After a long session a tab can be holding a dozen updates, and hearing all of them is a wall of mostly-stale speech; you want the last couple of things that happened. So it counts the rest instead of reading them — *"8 older updates skipped. probot: the plan phase is committed. … Next. berkshire: deployed, the page is live"* — oldest-first inside that window, so **the last words you hear are the current state**. `/tts recap_max <n>` moves the line. The skipped ones are still marked played and the tab still clears, so the next click isn't the same wall again; **`repeat 10` or `repeat 30m` is how you get them back**, since a numbered pop ignores the played flag.

**Purple is the exception to all of that.** Every other color means a terminal is holding something *for* you; purple means it has stopped and is waiting *on* you — an open question. So it outranks the waiting ladder on the same tab, and it never ages, because a decision doesn't get more urgent by being ignored. It shows only while that tab is in the background: switch away from an open question and it goes purple, switch back and the tint drops. When one opens unfocused the question's headers are read aloud ("waiting on your call. Scope, Delivery") — unless you're on `hold`, which means nothing speaks at a terminal you aren't watching, questions included.

**Click into an open question and it reads you the question**, in full, with the options you're choosing between — *"claude speaker is asking. Which readouts should the cap apply to? Options: click-in and bare recap; click-in only; bare recap only."* The one state that means *you're* the blocker was also the only one you couldn't hear on demand: the purple cue fires once, unfocused, and only reads headers. Summaries have click-to-talk; questions do too.

**A multi-question ask is followed one question at a time**, the way the UI shows them (`/tts ask_follow`). The default, `screen`, watches the question you're actually on: while you sit on the asking tab, the watcher asks the terminal for its visible text — one AppleScript query per 1.5-second poll, *only* in that state, so it costs nothing the rest of the time — and whether you advance with Enter, the arrow keys, or a click, it reads each question as you reach it and **cuts the readout the moment you move past one**. Works on Terminal.app and iTerm2; anywhere the screen can't be read it falls back to `click`: each click-in reads the next *unheard* question — click in, hear one, switch away, click back, hear the next. `off` keeps it to the first question. An already-heard set re-reads only while `/tts ask_reads <n>` allows (default 1 — the questions have been in front of you; repeating them is nagging). The tab still goes purple every time you switch away, since that tracks the question being *open*, not unheard. Answering stops a readout mid-sentence, and so does switching away from the tab or moving past the question — leaving it is the stop button. Only a read that *finished* spends one of your `ask_reads`: any readout cut mid-sentence — arrowed past, or switched away from — is un-heard and reads again when you come back, because three words of a question is not a heard question. Unlike the unprompted cue this **isn't** silenced by `hold`: clicking in is you asking. `/tts focus off` (no click-to-talk) or `/tts ask off` does silence it.

**The first read opens with the *why*** (`/tts ask_context`, default on): before question one, the voice speaks the words Claude wrote right before asking — sanitized and capped exactly like a spoken summary — because the question text alone rarely says why it's being asked. Option descriptions are still never read; those you can skim. And `/tts ask_first on` reads question one the moment a set opens in the tab you're already looking at, no click-in needed — off by default, but unlike a summary (which `rr` replays) a missed question has no replay, so hearing it immediately is a legitimate preference.

Buried behind another app? The **menu bar** carries the same state — `🔊 the docs terminal` while speaking, `🟡 2 waiting` while held — and a summary that *couldn't* be spoken raises a **notification banner**. Neither can take focus.

## When things speak: `chime` · `follow` · `hold`

| Mode | Voice free | Voice busy |
|---|---|---|
| **`chime`** *(default)* | speaks now | chimes when the other finishes, then waits |
| **`follow`** | speaks now | queues and auto-reads next, in finish order |
| **`hold`** | **speaks only if you're watching that terminal**, else waits | **waits** |

`hold` is for when a voice would break your concentration: nothing speaks at a terminal you aren't looking at. Every summary chimes softly, colors its tab, and waits to be clicked into. The one exception is presence — if the summary lands in the terminal that's focused *right then*, it reads out on the spot, because clicking off and back just to hear it is the same interruption with extra steps. `/tts focus off` turns that off along with click-to-talk; `/tts chime off` drops even the tone and leaves color alone.

Set per terminal or for everything: `/tts collision <chime|follow|hold> [global]`.

Follow mode only auto-reads summaries queued by follow-mode sessions in the last 5 minutes — older backlog and anything held during a call waits for you rather than surprising you later.

## Missed it? `repeat` — free replay

Type **`repeat`** (or **`rr`**) and it plays again, costing nothing. A `UserPromptSubmit` hook recognizes the trigger, speaks it, and exits 2 — which makes Claude Code **erase the prompt and never call the model**. No turn, no tokens, no context growth, the same "free to look at" feel as `/context`.

| Type | What happens |
|---|---|
| `repeat` | This session's most recent summary, again |
| **`repeat 3`** | **Pop the last 3 updates off the stack** — any number |
| **`repeat 5m`** | **Pop the last 5 minutes** — `s` / `m` / `h` |
| `repeat full` | **The whole response**, not the one-line summary |
| `repeat inverse` | Whichever rendering you *didn't* get — full if you're in `summary` mode, summary if you're in `full` |
| `repeat all` | Every session's unplayed summaries |
| `repeat status` | Lists this session's queue, speaks nothing |
| `shh` / `hush` / `repeat stop` | Shut the voice up right now |
| `stop` | Same — but only while the voice is talking; otherwise it's your prompt |

**The queue is a stack.** Every finished turn pushes one update down; a replay pops however many you ask for off the top. That's why the bound is a *count* and not a cutoff — you came back to the desk and want the last two or three things that happened, not "everything newer than some age". `repeat 3` gives you exactly three, oldest of them first so it plays in the order it happened. The time form is there for when you think in "since I walked away" instead of in updates.

A numbered pop deliberately **ignores whether you already heard it**: "the last 3" means the last 3. Reading them marks them played, so a later bare `repeat all` won't say them twice. A number bigger than the stack just gives you the whole stack.

That's also why **`repeat all` is capped and `repeat 3` isn't.** The bare form speaks the newest 3 and counts the rest ("8 older updates skipped") — it's the one that would otherwise read a whole session back at you. When you name a number you get that number, whatever it is. So a count is both the thing that limits the readout *and* the escape hatch from it: `repeat 10` hears everything the cap skipped, and `repeat 30m` does it by time.

**Five names, one command.** `rr`, `repeat`, `replay`, `recap` and `tts` each take that whole list — `rr full`, `replay full` and `recap full` are the same thing. A replay you can't remember the word for is a replay you don't use, and an alias costs one dict entry. The slash commands are untouched: `/tts`, `/recap` and `/spoken-recap` are never bare words, and `tts off` still reaches Claude, so configuring the voice works normally.

`full` and `inverse` are free for the same reason: the hook payload carries this session's transcript path, so the turn is re-rendered from disk with the same sanitizer that spoke it. Heard the summary, want the detail? You get it without a model call.

Only those exact strings are intercepted — *"repeat full coverage for the auth tests"* goes to Claude untouched. `again` is deliberately **not** a trigger: it far more often means "do that again" than "say that again". For the same reason nothing is bound to <kbd>Esc</kbd>, which already interrupts the agent — a key that sometimes cancels your work and sometimes only silences audio is a key you stop trusting.

## On a call? It stays quiet

If your **microphone or camera is in use** — Zoom, Teams, FaceTime, a Meet tab, a screen recording — nothing plays at all, not even the chime. The summary just waits. A chime already deferred behind another readout re-checks at the moment it would actually play, so a call starting *during* the wait still silences it.

**Answering a call mid-sentence stops the voice.** The check above gates speech before it *starts*, which left the one case it most needed to cover: a readout already in flight when you pick up. It used to keep talking into the meeting until the text ran out. Now a guard rides every readout and cuts it the moment the mic goes live — and what it was saying goes back to waiting, so an interrupted summary is held exactly like one that arrived during the call, tinting its tab and reading out afterwards. Being cut off is not the same as having heard it.

**When the call ends, the tab you're sitting on reads out what it took during it.** A summary that landed while you sat still through a meeting has no focus transition to hang a read off — that's what leaves a tab tinted red with nothing coming — so the call ending is treated as a focus-in on wherever you are. Only on that tab: a meeting ending must never start a voice at a terminal across the desk. Everything held elsewhere keeps its tint and waits to be clicked into, exactly as before.

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
| `/tts recap_max <n>` | Updates a backlog readout speaks (default 3; `repeat 10` is never capped) |
| `/tts ask_reads <n>` | Times an already-heard question set re-reads on click-in (default 1) |
| `/tts ask_follow <screen\|click\|off>` | Multi-question asks: follow the question on screen (default), advance one per click-in, or first question only |
| `/tts ask_first <on\|off>` | Read question one the moment a set opens in the focused tab (default off) |
| `/tts ask_context <on\|off>` | Open the first read with the words Claude wrote before asking — the why (default on) |
| `/tts raise <window\|off>` | Opt-in window raise (see caveat below) |
| `/tts voice <name\|default>` | Which `say` voice speaks — `say -v ?` lists what's installed |
| `/tts rate <n\|default>` | Words per minute for `say -r` (default: the system rate — plain `say` does not inherit the Accessibility rate slider) |
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
                        ├─ hold, watching it ▶ /usr/bin/say — you're right there
                        ├─ hold, elsewhere ─▶ chime, tab waits green→yellow→red
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

Only one thing speaks at a time, and that turn-taking is enforced by a single lock: whichever `say` is running. That makes a `say` that never *finishes* everything's problem — and `say` can hang indefinitely inside CoreAudio if the audio device is retargeted mid-utterance, which is what answering a call on a headset does. So the lock is bounded by age as well as liveness: a `say` still holding it long after its own text could possibly have been read is treated as wedged, killed, and the voice handed on. The bound is five minutes *or* twice the utterance's estimated length, whichever is longer, so it can never truncate a real readout — it exists only to stop one stuck process from muting every session until you notice.

### The window-raise caveat

`/tts raise window` brings the speaking window to the top, and is **off by default** for a reason worth knowing: macOS offers no raise that's guaranteed focus-free. `activate` steals focus across apps outright and is never used. `AXRaise` doesn't cross apps — but inside an app it makes the raised window *key*, so the next thing you type in that app can land there. It's skipped whenever your terminal app is already frontmost, so typing in a terminal is never interrupted. Want zero window movement, leave it off.

It also needs Accessibility permission (System Settings → Privacy & Security → Accessibility). Without it the raise silently does nothing; `speak-response.py --check-raise` tells you which state you're in.

## Hacking on it: `--link` and `--check`

The hooks never run this repo. They run `~/.claude/scripts/*.py`, the copy `install.sh` makes — so an edit to the file that *runs* is invisible to git, and an edit to the file in git changes nothing until you reinstall. Both have happened here, in the same afternoon: a fix committed to a file nothing executes, and ~290 lines of working, unversioned feature sitting in the installed copy one `install.sh` run from deletion.

```bash
./install.sh --link     # symlink the six managed files instead of copying
./install.sh --check    # is what's running what's committed?
```

`--link` removes the second copy. There is one file: edits are live on the next hook invocation — every hook is a fresh process — and anything uncommitted shows up in `git status` like any other change. Nothing else moves, because no state rides on the script's location (every path hangs off `CLAUDE_DIR`) and the re-exec paths use `abspath(__file__)`, which doesn't resolve symlinks, so child processes still run through `~/.claude`.

**It ties the hooks to this directory.** Move or rename the clone and they break silently — `--check` reports it as `BROKEN LINK`, and re-running `install.sh --link` from the new location repairs it. That's why plain copy stays the default; `--link` is for working on the tool, not for using it.

**The compiled helpers are the one thing linking can't keep live.** `av-status` is built from `av-status.c`; linking a `.py` makes edits current the moment they're saved, but a `.c` still has to be compiled, so pulling a C fix used to leave the old logic running with nothing to show for it — the helper kept working, just wrongly. So `speak-response.py` now rebuilds `av-status` itself whenever the source is newer than the binary: two `stat` calls on the normal path, one ~0.7s compile the first time after a pull, atomic replace so a hook firing mid-build never sees a half-written file. No compiler, no source, or a source that doesn't compile all leave the existing binary alone. Nobody has to know there's a build step. The Swift badge has no such hook and still needs `install.sh`.

`--check` reports `linked` / `identical` / `DRIFTED` / `BROKEN LINK` / `LINKED ELSEWHERE` / `MISSING` per file, compiles the three scripts, reports each compiled helper as `built` / `STALE` / `NOT BUILT`, and exits nonzero on any of it. Under `--link` the compile pass is the part that matters: a syntax error in the repo is live the moment you save it. And in the copy path, an installed file that differs is saved to `<name>.bak-<timestamp>` before it's overwritten — drift is never silently destroyed, only ever set aside.

Set **`CLAUDE_DIR`** to run the whole tool against a scratch directory — its own queue, state, tints, locks and pid file, touching nothing live:

```bash
mkdir -p /tmp/iso/scripts && ln -s "$PWD"/scripts/*.py /tmp/iso/scripts/
CLAUDE_DIR=/tmp/iso python3 scripts/tts-recap.py --status
```

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
rm -rf ~/.claude/tts-sessions ~/.claude/tts-tabcolor ~/.claude/tts-asking
```

Then remove the four entries that run these scripts from `~/.claude/settings.json` (or via `/hooks`) — one each under `Stop`, `UserPromptSubmit`, `PreToolUse` and `PostToolUse` — and the "Spoken Summary (TTS)" section from `~/.claude/CLAUDE.md`.

## License

MIT
