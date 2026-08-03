#!/usr/bin/env python3
"""Stop hook: speak Claude's response aloud via macOS `say` (on-device TTS).

Modes (per-session override > global default > "summary"):
  off     - stay silent
  summary - speak the final line(s) marked with 🔊, else a sanitized fallback;
            either way trimmed to the resolved summary length (see below)
  full    - speak the entire sanitized response, uncapped (🔊 marker line dropped)

Summary length: the summary is trimmed to a character cap. By default the
cap is ADAPTIVE — proportional to the turn's sanitized output volume:
clamp(chars * SUMMARY_RATIO, SUMMARY_FLOOR, SUMMARY_CEILING) — so a big
multi-step turn earns a longer spoken summary and a one-liner stays short.
An explicit `/tts length <n>` (per-session > global "summary_chars") overrides
the formula with a flat cap. Applies to both the authored 🔊 line and the
fallback; `full` mode ignores it.

State: global mode in ~/.claude/tts-state.json {"mode": "summary"};
per-session overrides in ~/.claude/tts-sessions/<session_id>.json.
Toggled by the /tts skill.

Delivery: every summary is appended to ~/.claude/tts-queue.jsonl. The
"collision" setting (per-session override > global > "chime") decides
what happens to it:
  chime  - speak immediately if the voice is free; if another session is
           talking, chime after that speech ends and leave the summary
           waiting (spoken: false)
  follow - same, except a waiting summary is spoken automatically right
           after the current speech ends (a locked drainer subprocess,
           `speak-response.py --drain`, serializes the readout)
  hold   - never speak unprompted at a terminal you are not looking at:
           chime, color the tab, and wait to be clicked into. For when you
           are deep in something and a voice would break it. A summary
           landing in the terminal that is focused RIGHT NOW still speaks
           on the spot — being already there counts as being there.
Any waiting summary can also be replayed with `repeat`/`rr` or
/spoken-recap (scripts/tts-recap.py).
If the mic or camera is
live (a call, a recording — checked via the compiled av-status helper),
nothing plays at all, not even the chime; the entry just queues. Always
exits 0 — TTS must never block the session.

Visual cue (Terminal.app tab background via AppleScript, iTerm2 tab color
via OSC 6; any other emulator just speaks). The terminal itself tells you
what it wants:
  blue    - reading out right now
  purple  - an AskUserQuestion is open: it is waiting on YOUR answer
  green   - has a summary ready and waiting
  yellow  - has been waiting over 30s
  red     - has been waiting over 5min

Purple is the only color that means "you are the blocker", so it outranks
the waiting ladder on the same tab, and it is shown ONLY while that tab is
in the background — the question is already on screen in the tab you are
looking at, so tinting it would be noise. Switch away from an open question
and it goes purple; switch back and the tint drops (the question stays open
either way). When it first opens unfocused, the question's headers are read
aloud (`ask_speak`), so you know what is being asked without looking. Both
off via `/tts ask_color off` / `/tts ask_speak off`. A question closed
WITHOUT an answer (typed over, interrupted) fires no close hook; its marker
is dropped as soon as the session's transcript moves on without it —
otherwise a long turn would sit purple for its whole run and click-ins
would re-read a dead question. Answering a question stops any readout of
it still mid-sentence, and so does switching away from the tab — those are
the two stop gestures.
Focus a waiting terminal and it reads out on the spot, whatever its age,
then goes back to its own color — the newest `recap_max` (3) of what it
holds, oldest-first inside that window so the last thing you hear is the
newest, with anything older counted rather than read ("8 older updates
skipped"). Focus a terminal holding an open QUESTION and it reads the
question out too, options included — the state that means you are the
blocker is the one most worth hearing on demand. A multi-question set is
followed one question at a time, matching how the UI shows them
(`ask_follow`): `screen` (default) reads the tab's visible text while you
sit on it — one AppleScript query per 1.5s poll, ONLY in that state — and
keeps the voice on the sub-question actually displayed, reading each new
one as you advance (Enter, arrows, click alike) and cutting over the
moment the screen moves on; `click` skips the scraping and each click-in
reads the next unheard question instead; `off` is first-question-only.
`screen` behaves like `click` wherever the screen cannot be read.
`ask_reads` (default 1) caps how often an already-heard set re-reads; a
readout cut mid-sentence (skipped past, switched away from) is un-heard
and reads again on return. The set's first read opens with the words
Claude wrote right before asking — the why, capped like a summary
(`ask_context`, default on) — and `ask_first` (default off) reads
question one the moment a set opens in the focused tab, no click needed.
The aging and the focus read are done by a single locked
watcher (`speak-response.py --watch`) that starts when something first
has to wait and exits as soon as the queue is clear — nothing polls in
the background during normal use. Colors off via `/tts color off`, focus
reads off via `focus_speak`.

Every tint parks the terminal's real color in
~/.claude/tts-tabcolor/<tty>.json, and a tint is only ever dropped once
the repaint is confirmed, so no crash or kill can strand a terminal in a
color (`--repair` is the last resort if one ever does).

Freshness: the hook runs async and can beat Claude Code's transcript
flush, so it fingerprints the last-handled message per session
(~/.claude/tts-sessions/<session_id>.seen) and waits for the transcript
to move past it — otherwise it would speak the previous turn.
"""
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time

# Every path below hangs off this, so pointing CLAUDE_DIR at a temp dir
# gives you a complete, isolated instance: its own queue, state, tints,
# locks and pid file, touching nothing live. That is the difference
# between a change you can test and a change you can only ship — the
# alternative is commandeering the real queue and the real terminals,
# which is why edits used to gravitate to the installed copy instead.
CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude")
PID_FILE = os.path.join(CLAUDE_DIR, "scripts", ".speak-response.pid")
STATE_FILE = os.path.join(CLAUDE_DIR, "tts-state.json")
SESSION_DIR = os.path.join(CLAUDE_DIR, "tts-sessions")
QUEUE_FILE = os.path.join(CLAUDE_DIR, "tts-queue.jsonl")
AV_HELPER = os.path.join(CLAUDE_DIR, "scripts", "av-status")
# Where the C source for that helper lives. Under `install.sh --link` this
# file is a symlink into the repo, so realpath() lands in the repo's
# scripts/ and the source sits right next to it; under a plain `cp`
# install it lands in ~/.claude/scripts, where no .c is ever copied, and
# the staleness check below finds nothing and does nothing. That asymmetry
# is correct: a copied install only changes when install.sh runs, and
# install.sh already rebuilds. A linked install is the one that can drift,
# because editing the .py is live instantly while the compiled helper is
# whatever was last built.
AV_SOURCE = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                         "av-status.c")
AV_BUILD = ["cc", "-O2", "-o", None, AV_SOURCE, "-framework", "CoreAudio",
            "-framework", "CoreMediaIO", "-framework", "CoreFoundation"]
DRAIN_LOCK = os.path.join(CLAUDE_DIR, "scripts", ".tts-drain.lock")
TABCOLOR_DIR = os.path.join(CLAUDE_DIR, "tts-tabcolor")
ASKING_DIR = os.path.join(CLAUDE_DIR, "tts-asking")
# Speaking tint. Dark enough that white terminal text stays readable.
TAB_COLORS = {"red": "#550000", "orange": "#553300", "yellow": "#4d4d00",
              "green": "#004d1a", "blue": "#00304d", "purple": "#3d0055"}
DEFAULT_TAB_COLOR = "blue"  # while actually speaking
# A summary that could not be spoken yet ages in place: green when it is
# ready to talk, yellow once it has been waiting a while, red once it has
# been waiting a long time. Focus the terminal and it reads out.
WAIT_STAGES = ((0, "green"), (30, "yellow"), (300, "red"))
# How many updates a backlog readout actually speaks. After a long session
# the queue behind a tab can be dozens of entries, and reading all of them
# is a wall of mostly-stale speech. Read the newest few, oldest-first so
# the last thing you hear is the current state, and say up front how many
# were skipped. Skipped entries are still marked played (the tab clears);
# `repeat 10` / `repeat 30m` is the way back to them.
RECAP_MAX_DEFAULT = 3
# An open AskUserQuestion: the session is blocked on Justin, not the other
# way round. Deliberately off the green→yellow→red ladder so it reads as a
# different KIND of state, not a further stage of waiting. Static (a
# decision does not get more urgent by being ignored, it just stays yours)
# and background-only — see the module docstring.
ASK_TAB_COLOR = "purple"
ASK_MAX_AGE_SECS = 86400  # forget an ask marker nothing ever closed
# How long the transcript may keep growing after a question opens before
# growth means the question is over. The tool_use entry itself can flush
# a beat after the marker is written; anything later means the turn moved
# on — an open question BLOCKS the session, so its transcript sits still.
ASK_STALE_GRACE_SECS = 10
# Times clicking into a terminal re-reads the question it is holding.
# Once: the click that focused the tab put the question on screen, and
# repeating it is nagging — `/tts ask_reads N` for more. The purple tab
# still comes back on every switch away, because that tracks the question
# being OPEN, not unheard.
ASK_READS_DEFAULT = 1
# How the voice advances through a multi-question ask. `screen`: while you
# sit on the asking tab, the watcher reads the tab's visible text (one
# AppleScript query per poll, ONLY in that state) and keeps the voice on
# the sub-question actually displayed — advance and it cuts over. `click`:
# no scraping; each click-in reads the next unheard question instead.
# `off`: click-ins read the first question only. `screen` quietly behaves
# like `click` wherever the screen cannot be read (emulator without
# AppleScript text access, scrape miss).
ASK_FOLLOWS = ("screen", "click", "off")
ASK_FOLLOW_DEFAULT = "screen"
# A question is matched on this many normalized characters of its text —
# and never on fewer than ASK_NEEDLE_MIN, which would match on noise.
ASK_NEEDLE_CHARS = 40
ASK_NEEDLE_MIN = 12
WATCH_LOCK = os.path.join(CLAUDE_DIR, "scripts", ".tts-watch.lock")
BADGE_FILE = os.path.join(CLAUDE_DIR, "tts-badge.txt")
BADGE_HELPER = os.path.join(CLAUDE_DIR, "scripts", "speaking-badge")
STAGE_EMOJI = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
WATCH_POLL_SECS = 1.5
WATCH_MAX_AGE_SECS = 86400  # forget pending entries older than a day
# Every color this tool ever paints. Used to recognize a tab we stranded
# (so we never save a tint as a tab's "original") and to repair one.
TINTS = frozenset(tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
                  for h in TAB_COLORS.values())
# Adaptive summary cap: proportional to the turn's sanitized volume, clamped.
# An explicit /tts length (summary_chars) overrides this with a flat cap.
SUMMARY_RATIO = 0.5
SUMMARY_FLOOR = 400
SUMMARY_CEILING = 2500
MARKER = "🔊"
MODES = ("off", "summary", "full")
COLLISIONS = ("chime", "follow", "hold")
RAISES = ("off", "window")
FRESH_WAIT_SECS = 8  # must stay under the hook timeout in settings.json
FOLLOW_WINDOW_SECS = 300  # drainer ignores entries older than this


def read_setting(path, key, valid):
    try:
        with open(path) as f:
            val = (json.load(f).get(key) or "").strip()
        return val if val in valid else None
    except (OSError, ValueError):
        return None


def resolve_setting(session_id, key, valid, default):
    if session_id:
        val = read_setting(os.path.join(SESSION_DIR, f"{session_id}.json"),
                           key, valid)
        if val:
            return val
    return read_setting(STATE_FILE, key, valid) or default


def resolve_mode(session_id):
    return resolve_setting(session_id, "mode", MODES, "summary")


def resolve_collision(session_id):
    return resolve_setting(session_id, "collision", COLLISIONS, "chime")


def resolve_raise(session_id):
    # Default OFF: raising a window can never be guaranteed focus-free —
    # see raise_window(). The tint is the cue; the raise is opt-in.
    return resolve_setting(session_id, "raise", RAISES, "off")


def explicit_int(session_id, key):
    """A positive int setting: per-session file > global state file.

    The numeric counterpart to resolve_setting() — that one .strip()s what
    it reads and matches it against a tuple of allowed strings, so it can
    only ever resolve enums. Anything that is not a positive number
    (missing, zero, non-numeric, bool) is ignored and the search falls
    through. None means "not explicitly set", leaving the caller free to
    pick a different fallback per setting: adaptive for summary_chars, a
    plain constant for the rest.
    """
    paths = []
    if session_id:
        paths.append(os.path.join(SESSION_DIR, f"{session_id}.json"))
    paths.append(STATE_FILE)
    for path in paths:
        try:
            with open(path) as f:
                val = json.load(f).get(key)
        except (OSError, ValueError):
            continue
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            return int(val)
    return None


def adaptive_cap(text):
    """Summary cap proportional to the turn's sanitized (spoken) volume."""
    volume = len(sanitize(text))
    return int(min(SUMMARY_CEILING, max(SUMMARY_FLOOR, volume * SUMMARY_RATIO)))


def resolve_summary_cap(session_id, text):
    """Explicit /tts length cap if set, else the adaptive proportional cap."""
    return explicit_int(session_id, "summary_chars") or adaptive_cap(text)


def resolve_recap_max(session_id):
    """How many updates a backlog readout speaks. See RECAP_MAX_DEFAULT."""
    return explicit_int(session_id, "recap_max") or RECAP_MAX_DEFAULT


def resolve_ask_reads(session_id):
    """How many times a click-in re-reads an open question."""
    return explicit_int(session_id, "ask_reads") or ASK_READS_DEFAULT


def resolve_ask_follow(session_id):
    """How the voice advances through a multi-question ask."""
    return resolve_setting(session_id, "ask_follow", ASK_FOLLOWS,
                           ASK_FOLLOW_DEFAULT)


def resolve_voice(session_id):
    """`say -v` name, or None to leave it unset (macOS's own system voice).

    `voice` per-session > global > unset — same "stop at the first level
    that HAS a value, even if that value is the disabling sentinel" shape
    as resolve_tab_color()'s "off". An earlier version returned on any
    non-"default" string, which meant a session explicitly set back to
    "default" silently fell through to a global custom voice instead of
    clearing it — the one case the setting exists for.

    Non-string values are ignored rather than coerced: set_setting() only
    ever writes a string here, so a number in the file means the file was
    hand-edited, and `say -v 42` would speak nothing, silently, exactly
    the failure the set-time validation exists to prevent.
    """
    paths = []
    if session_id:
        paths.append(os.path.join(SESSION_DIR, f"{session_id}.json"))
    paths.append(STATE_FILE)
    raw = None
    for path in paths:
        try:
            with open(path) as f:
                val = json.load(f).get("voice")
        except (OSError, ValueError):
            continue
        val = val.strip() if isinstance(val, str) else ""
        if val:
            raw = val
            break
    if not raw or raw.lower() == "default":
        return None
    return raw


def resolve_rate(session_id):
    """Words per minute for `say -r`, or None to leave it at the system rate.

    Not explicit_int(): that helper falls through past anything that isn't
    a positive number, including an explicit "default", so a session set
    back to "default" would silently inherit a global rate instead of
    clearing it — same bug class as resolve_voice() above, same fix shape.
    """
    paths = []
    if session_id:
        paths.append(os.path.join(SESSION_DIR, f"{session_id}.json"))
    paths.append(STATE_FILE)
    for path in paths:
        try:
            with open(path) as f:
                val = json.load(f).get("rate")
        except (OSError, ValueError):
            continue
        if val is None:
            continue
        if isinstance(val, str) and val.strip().lower() == "default":
            return None
        if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
            return int(val)
    return None


def say_argv(session_id, text):
    """Full `say` argv for one utterance: voice/rate flags before the text,
    exactly as every call site used to hardcode `["/usr/bin/say", text]`."""
    argv = ["/usr/bin/say"]
    voice = resolve_voice(session_id)
    if voice:
        argv += ["-v", voice]
    rate = resolve_rate(session_id)
    if rate:
        argv += ["-r", str(rate)]
    argv.append(text)
    return argv


def last_assistant_text(transcript_path):
    """(newest assistant text, count of assistant text messages seen)."""
    text = None
    n = 0
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content") or []
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if any(p.strip() for p in parts):
                text = "\n".join(parts)
                n += 1
    return text, n


def fresh_assistant_text(transcript_path, session_id):
    """Newest assistant text not already handled for this session, or None.

    The Stop hook runs async, so it can fire before Claude Code finishes
    flushing the turn's final message to the transcript — a naive read then
    speaks the PREVIOUS turn (one turn stale), or a mid-turn status note
    whose lack of a 🔊 line triggers the long fallback. So: remember a
    (count, hash) fingerprint of the last text we handled, poll until the
    transcript moves past it, then let the file settle so we take the
    turn's final message, not an intermediate one. Nothing new by the
    deadline (duplicate Stop fire, empty turn) → None → stay silent.
    """
    seen_path = os.path.join(SESSION_DIR, f"{session_id}.seen")
    try:
        with open(seen_path) as f:
            seen = f.read().strip()
    except OSError:
        seen = ""

    def fingerprint(text, n):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return f"{n}:{digest}"

    deadline = time.time() + FRESH_WAIT_SECS
    while True:
        try:
            text, n = last_assistant_text(transcript_path)
        except OSError:
            return None
        if text and fingerprint(text, n) != seen:
            break
        if time.time() >= deadline:
            return None
        time.sleep(0.4)
    while time.time() < deadline:  # settle: transcript may still be growing
        time.sleep(0.6)
        try:
            newer = last_assistant_text(transcript_path)
        except OSError:
            break
        if newer == (text, n) or not newer[0]:
            break
        text, n = newer
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        with open(seen_path, "w") as f:
            f.write(fingerprint(text, n))
    except OSError:
        pass
    return text


def sanitize(text):
    # Drop fenced code blocks entirely.
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    # Drop table rows and horizontal rules.
    lines = [ln for ln in text.splitlines()
             if not ln.lstrip().startswith("|") and not re.fullmatch(r"\s*[-*_]{3,}\s*", ln)]
    text = "\n".join(lines)
    # Links: keep the label, drop the URL; drop bare URLs.
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    # Strip markdown decoration: headers, emphasis, inline code, blockquotes.
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`>#]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _trim(spoken, cap):
    """Trim to `cap` chars at a word boundary, with a spoken 'see terminal' tail."""
    if not cap or len(spoken) <= cap:
        return spoken
    cut = spoken[:cap]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > 0 else cut) + " — full response is in the terminal."


def pick_speech(text, mode, cap):
    lines = text.splitlines()
    marker_line = next((ln.strip() for ln in reversed(lines)
                        if ln.strip().startswith(MARKER)), None)
    if mode == "full":
        body = "\n".join(ln for ln in lines if not ln.strip().startswith(MARKER))
        return sanitize(body)  # full is deliberately uncapped
    if marker_line:
        return _trim(sanitize(marker_line[len(MARKER):]), cap)
    return _trim(sanitize(text), cap)


def rebuild_av_if_stale():
    """Recompile av-status when its source is newer than the binary.

    The .py files go live the instant they are saved under a linked
    install; av-status is COMPILED, so a C fix reaches nobody until
    someone remembers to rebuild it. That asymmetry is invisible — the
    helper keeps working, just with the old logic — which is the worst
    shape for a bug to have. Nobody should have to know the tool has a
    build step.

    Costs two stat() calls on the common path. Builds to a temp file and
    os.replace()s it, so a hook that fires mid-build reads either the old
    binary or the new one, never a half-written one; concurrent builders
    each write their own temp and the last replace wins, all of them
    producing identical output, so no lock is needed. Every failure is
    silent and leaves the existing binary alone: no compiler, no source
    (copied install), unwritable directory, or a source that does not
    compile all mean "keep using what we have", exactly as install.sh
    treats a failed build as non-fatal.

    A MISSING binary is deliberately not built here — only a stale one is
    replaced. The first build belongs to install.sh, and attempting one on
    every call when there is no compiler would spawn a doomed subprocess
    on every single hook invocation.
    """
    try:
        if os.path.getmtime(AV_SOURCE) <= os.path.getmtime(AV_HELPER):
            return
    except OSError:
        return  # no source (copied install) or no binary yet — leave it
    tmp = f"{AV_HELPER}.new.{os.getpid()}"
    argv = [tmp if a is None else a for a in AV_BUILD]
    try:
        if subprocess.run(argv, capture_output=True,
                          timeout=60).returncode == 0:
            os.replace(tmp, AV_HELPER)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.unlink(tmp)  # no-op once the replace above consumed it
    except OSError:
        pass


def on_call():
    """True if the mic or camera is actively in use (call, recording).

    Asks the compiled av-status helper (scripts/av-status.c) — the same
    signals as the orange/green menu-bar dots, so it covers Zoom, Teams,
    FaceTime, and browser-tab calls alike. Missing helper or any failure
    means "not on a call": speech must degrade to normal, never to silence.
    """
    rebuild_av_if_stale()
    if not os.access(AV_HELPER, os.X_OK):
        return False
    try:
        out = subprocess.run([AV_HELPER], capture_output=True, text=True,
                             timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "mic=1" in out or "cam=1" in out


def active_say_pid():
    """Pid of the currently speaking `say`, or None if the voice is idle."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    try:
        # pids get reused — only counts as busy if it's still a `say` process.
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True).stdout.strip()
        return pid if out.endswith("say") else None
    except OSError:
        return None


def enqueue(entry):
    try:
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def humanize(name):
    """camelCase/dashes/underscores → speakable words."""
    name = re.sub(r"[-_]+", " ", name)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


def speak_name(project):
    """Spoken form of a project name: custom name from the global state
    file's "names" map if set, else camelCase/dashes split into words."""
    try:
        with open(STATE_FILE) as f:
            names = json.load(f).get("names") or {}
        if project in names:
            return names[project]
    except (OSError, ValueError):
        pass
    return humanize(project)


def session_name(session_id):
    """The session's title — /rename or Claude Code's auto title — from the
    live registry (<CLAUDE_DIR>/sessions/<pid>.json), newest entry wins."""
    sess_dir = os.path.join(CLAUDE_DIR, "sessions")
    best, best_ts = None, -1
    try:
        files = os.listdir(sess_dir)
    except OSError:
        return None
    for fn in files:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(sess_dir, fn)) as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        name = (info.get("name") or "").strip()
        ts = info.get("updatedAt") or 0
        if info.get("sessionId") == session_id and name and ts > best_ts:
            best, best_ts = name, ts
    return best


def resolve_tab_color(session_id):
    """RGB tuple to tint the speaking terminal with, or None if disabled.

    `tab_color` per-session > global > "red". Accepts a name from
    TAB_COLORS, a #rrggbb hex, or "off"/"none"/"false" to disable.
    """
    raw = None
    paths = [os.path.join(SESSION_DIR, f"{session_id}.json")] if session_id else []
    for path in paths + [STATE_FILE]:
        try:
            with open(path) as f:
                val = json.load(f).get("tab_color")
        except (OSError, ValueError):
            continue
        if isinstance(val, str) and val.strip():
            raw = val.strip().lower()
            break
    return parse_color(raw if raw is not None else DEFAULT_TAB_COLOR)


def resolve_ask_color(session_id):
    """RGB to tint a terminal holding an open question, or None if off.

    `ask_color` per-session > global > "purple". Same grammar as
    `tab_color`, so `/tts ask_color off` drops the cue without touching
    the speaking/waiting colors.
    """
    raw = None
    paths = [os.path.join(SESSION_DIR, f"{session_id}.json")] if session_id else []
    for path in paths + [STATE_FILE]:
        try:
            with open(path) as f:
                val = json.load(f).get("ask_color")
        except (OSError, ValueError):
            continue
        if isinstance(val, str) and val.strip():
            raw = val.strip().lower()
            break
    return parse_color(raw if raw is not None else ASK_TAB_COLOR)


def parse_color(raw):
    if raw in ("off", "none", "false"):
        return None
    hexval = TAB_COLORS.get(raw, raw)
    if not re.fullmatch(r"#[0-9a-f]{6}", hexval or ""):
        hexval = TAB_COLORS[DEFAULT_TAB_COLOR]
    return tuple(int(hexval[i:i + 2], 16) for i in (1, 3, 5))


def owning_tty():
    """The controlling terminal of the Claude Code session that fired this
    hook. "/dev/ttysNNN", or None if there is no tty."""
    return owning_tty_pid()[0]


def owning_tty_pid():
    """(tty, pid) of the Claude Code session that fired this hook: walk up
    from this process until a parent has a controlling terminal (the hook
    itself is spawned without one). That pid is the session's own — an ask
    marker holds it so a killed session can never strand a tinted tab.
    (None, None) if there is no tty."""
    pid = os.getpid()
    for _ in range(10):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.split()
        except OSError:
            return None, None
        if len(out) != 2:
            return None, None
        ppid, tty = out
        if tty != "??":
            return (tty if tty.startswith("/dev/") else "/dev/" + tty), pid
        try:
            pid = int(ppid)
        except ValueError:
            return None, None
        if pid <= 1:
            return None, None
    return None, None


def _osascript(script):
    try:
        return subprocess.run(["/usr/bin/osascript", "-e", script],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _terminal_tabs(body):
    """Wrap `body` in a walk over every Terminal.app tab, bound to `t`."""
    return ('tell application "Terminal"\n'
            "set AppleScript's text item delimiters to \",\"\n"
            "repeat with w in windows\nrepeat with t in tabs of w\ntry\n"
            f"{body}\n"
            "end try\nend repeat\nend repeat\nend tell")


def _terminal_bg(tty):
    """Apple Terminal: the tab's background color as 8-bit rgb, or None."""
    out = _osascript(_terminal_tabs(
        f'if (tty of t) is "{tty}" then return (background color of t) as text'))
    parts = out.split(",")
    if len(parts) != 3 or not all(p.strip().isdigit() for p in parts):
        return None
    return tuple(int(p) // 257 for p in parts)  # 16-bit → 8-bit


def _paint(tty, term, rgb):
    """Tint one terminal. rgb None = restore the emulator's own default
    (iTerm2 only; Terminal.app has no such reset, so it repaints the saved
    color instead). True if the paint command went out."""
    if term == "Apple_Terminal":
        if rgb is None:
            return False
        r, g, b = (min(65535, c * 257) for c in rgb)
        # Must report real success: a silently-failed repaint (osascript
        # timeout, Terminal busy) used to drop the restore record and
        # strand the tab red forever.
        return _osascript(_terminal_tabs(
            f'if (tty of t) is "{tty}" then\n'
            f"set background color of t to {{{r}, {g}, {b}}}\n"
            "return \"ok\"\nend if") + '\nreturn "no-tab"') == "ok"
    if term == "iTerm.app":
        if rgb is None:
            seq = "\033]6;1;bg;*;default\a"
        else:
            seq = "".join(f"\033]6;1;bg;{name};brightness;{val}\a"
                          for name, val in zip(("red", "green", "blue"), rgb))
        try:
            with open(tty, "w") as f:
                f.write(seq)
            return True
        except OSError:
            return False
    return False  # unsupported emulator: speak, just don't tint


AX_PROCESS = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm2"}

# Find the window holding `tty` and AXRaise it, in ONE osascript run.
# The handle is the window's z-order index, not its title: terminal titles
# carry a live spinner glyph and change between calls, so a name lookup
# would race. Both apps order `windows` front-to-back, same as System
# Events, so the index carries across.
RAISE_SCRIPT = {
    "Apple_Terminal": '''tell application "Terminal"
set idx to 0
repeat with w in windows
repeat with t in tabs of w
try
if (tty of t) is "%(tty)s" then set idx to index of w
end try
end repeat
end repeat
end tell''',
    "iTerm.app": '''tell application "iTerm2"
set idx to 0
set n to 0
repeat with w in windows
set n to n + 1
repeat with t in tabs of w
repeat with s in sessions of t
try
if (tty of s) is "%(tty)s" then set idx to n
end try
end repeat
end repeat
end repeat
end tell''',
}


def frontmost_app():
    """Name of the app that currently owns the keyboard, or ""."""
    return _osascript('tell application "System Events" to return name of '
                      "first application process whose frontmost is true")


def raise_window(tty, term):
    """Bring the speaking terminal's window to the top of the stack.

    OFF BY DEFAULT, and opt-in only, because macOS gives no way to raise a
    window that is guaranteed not to take the keyboard: AXRaise does not
    cross apps (and `activate`, which would, is never used), but inside an
    app the raised window becomes KEY — so the next thing you type in that
    app goes to it, not to the window you left. Two guards narrow that as
    far as it can go: skip the raise whenever the terminal app is already
    frontmost (typing in a terminal never gets pulled), and never raise
    from anywhere else. If you want zero window movement, leave it off.
    Needs Accessibility permission for the terminal app; without it this
    silently does nothing (`speak-response.py --check-raise` says so).
    """
    proc = AX_PROCESS.get(term)
    if not proc or not tty:
        return False
    if frontmost_app() == proc:
        return False  # you're typing in a terminal — never pull the rug
    return _osascript(f'''{RAISE_SCRIPT[term] % {"tty": tty}}
if idx is 0 then return "no-window"
tell application "System Events" to tell process "{proc}"
if idx > (count of windows) then return "no-ax-window"
perform action "AXRaise" of window idx
end tell
return "ok"''') == "ok"


def repair():
    """Repaint any tab left showing one of our tints (`--repair`).

    Last-resort cleanup for a tab whose restore record is gone, so the
    color it used to have is unknowable: fall back to the profile's own
    background color. Tabs with a live record are left alone — they may
    be legitimately speaking right now.
    """
    if (os.environ.get("TERM_PROGRAM") or "") != "Apple_Terminal":
        print("repair: Terminal.app only (iTerm2 tab colors reset themselves)")
        return
    held = {rec for rec in os.listdir(TABCOLOR_DIR)} if os.path.isdir(TABCOLOR_DIR) else set()
    default = _osascript('tell application "Terminal"\n'
                         "set AppleScript's text item delimiters to \",\"\n"
                         "return (background color of default settings) as text\n"
                         "end tell")
    parts = [p.strip() for p in default.split(",")]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        print("repair: could not read the profile's background color")
        return
    fixed = []
    for tty, rgb in _all_tab_colors():
        if rgb in TINTS and os.path.basename(tty) + ".json" not in held:
            if _paint(tty, "Apple_Terminal", tuple(int(p) // 257 for p in parts)):
                fixed.append(tty)
    print(f"repair: repainted {len(fixed) or 'no'} tab(s)"
          + (": " + " ".join(fixed) if fixed else ""))


def _all_tab_colors():
    """[(tty, (r,g,b)), ...] for every Terminal.app tab."""
    out = _osascript('set acc to ""\ntell application "Terminal"\n'
                     "repeat with w in windows\nrepeat with t in tabs of w\ntry\n"
                     "set c to background color of t\n"
                     "set acc to acc & (tty of t) & \" \" & (item 1 of c) & \" \" "
                     "& (item 2 of c) & \" \" & (item 3 of c) & linefeed\n"
                     "end try\nend repeat\nend repeat\nend tell\nreturn acc")
    tabs = []
    for line in out.splitlines():
        bits = line.split()
        if len(bits) == 4 and all(b.isdigit() for b in bits[1:]):
            tabs.append((bits[0], tuple(int(b) // 257 for b in bits[1:])))
    return tabs


# Every setting the wizard (and /tts) can touch: key -> allowed values.
# "color" and "int" are validated specially. Keeping this in one place
# means a caller never has to know the file layout or merge JSON by hand.
SETTABLE = {"mode": MODES, "collision": COLLISIONS, "chime": ("on", "off"),
            "focus_speak": ("on", "off"), "menubar": ("on", "off"),
            "notify": ("on", "off"), "raise": RAISES,
            "tab_color": "color", "ask_color": "color",
            "ask_speak": ("on", "off"), "summary_chars": "int",
            "recap_max": "int", "ask_reads": "int",
            "ask_follow": ASK_FOLLOWS, "ask_first": ("on", "off"),
            "ask_context": ("on", "off"),
            "voice": "voice", "rate": "rate"}
DEFAULTS = {"mode": "summary", "collision": "chime", "chime": "on",
            "focus_speak": "on", "menubar": "on", "notify": "on",
            "raise": "off", "tab_color": DEFAULT_TAB_COLOR,
            "ask_color": ASK_TAB_COLOR, "ask_speak": "on",
            "summary_chars": "adaptive", "recap_max": RECAP_MAX_DEFAULT,
            "ask_reads": ASK_READS_DEFAULT, "ask_follow": ASK_FOLLOW_DEFAULT,
            "ask_first": "off", "ask_context": "on",
            "voice": "default", "rate": "default"}


def _settings_path(session_id):
    if not session_id:
        return STATE_FILE
    os.makedirs(SESSION_DIR, exist_ok=True)
    return os.path.join(SESSION_DIR, f"{session_id}.json")


def installed_voices():
    """Every voice name `say -v` knows about, macOS's own multi-language list.

    `say -v NotARealName` exits 0 and speaks nothing — no error, no fallback
    — so a typo'd voice would go silently unheard forever rather than fail
    once at set time. Parsed on the locale token (`xx_XX` / `xx_001`, always
    present, never containing whitespace) rather than column width: several
    names ("Eddy (English (US))") are wider than the padding, so a fixed
    number of spaces before the locale field is not reliable.
    """
    try:
        out = subprocess.run(["/usr/bin/say", "-v", "?"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    names = set()
    for line in out.splitlines():
        m = re.match(r"^(.*)\s([a-z]{2}_[A-Za-z0-9]+)\s+#", line)
        if m:
            names.add(m.group(1).strip())
    return names


def set_setting(key, value, session_id=None):
    """Merge one setting into the session or global file. Returns a message."""
    allowed = SETTABLE.get(key)
    if allowed is None:
        return f"unknown setting {key!r} — one of: {', '.join(sorted(SETTABLE))}"
    if allowed == "int":
        try:
            value = int(value)
        except (TypeError, ValueError):
            return f"{key} wants a positive integer, got {value!r}"
        if value <= 0:
            return f"{key} wants a positive integer, got {value!r}"
    elif allowed == "color":
        value = str(value).strip().lower()
        if value not in ("off", "none", "false") and value not in TAB_COLORS \
                and not re.fullmatch(r"#[0-9a-f]{6}", value):
            return (f"{key} wants off, a #rrggbb hex, or one of: "
                    + ", ".join(TAB_COLORS))
    elif allowed == "voice":
        value = str(value).strip()
        if value.lower() != "default" and value not in installed_voices():
            return (f"{key}: {value!r} is not an installed voice — "
                     "`say -v ?` lists them, or `default` to clear this")
    elif allowed == "rate":
        value = str(value).strip()
        if value.lower() != "default":
            try:
                value = int(value)
            except (TypeError, ValueError):
                return f"{key} wants a positive integer or 'default', got {value!r}"
            if value <= 0:
                return f"{key} wants a positive integer or 'default', got {value!r}"
    elif value not in allowed:
        return f"{key} wants one of: {', '.join(allowed)} (got {value!r})"
    path = _settings_path(session_id)
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = value
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
    except OSError as exc:
        return f"could not write {path}: {exc}"
    return f"{key} = {value} ({'this session' if session_id else 'global'})"


def show_settings(session_id=None):
    """Print every setting, its value, and where that value came from."""
    print(f"{'setting':<14} {'value':<12} source")
    for key in SETTABLE:
        session_val = global_val = None
        if session_id:
            try:
                with open(_settings_path(session_id)) as f:
                    session_val = json.load(f).get(key)
            except (OSError, ValueError):
                pass
        try:
            with open(STATE_FILE) as f:
                global_val = json.load(f).get(key)
        except (OSError, ValueError):
            pass
        if session_val not in (None, ""):
            value, source = session_val, "this session"
        elif global_val not in (None, ""):
            value, source = global_val, "global"
        else:
            value, source = DEFAULTS.get(key, "-"), "default"
        print(f"{key:<14} {str(value):<12} {source}")


def check_raise():
    """Report whether window-raising can actually work here (used by /tts)."""
    term = os.environ.get("TERM_PROGRAM") or ""
    proc = AX_PROCESS.get(term)
    if not proc:
        print(f"raise: unsupported terminal ({term or 'unknown'}) — "
              "Terminal.app and iTerm2 only")
        return
    try:
        res = subprocess.run(
            ["/usr/bin/osascript", "-e",
             f'tell application "System Events" to return count of '
             f'windows of process "{proc}"'],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"raise: cannot run osascript ({exc})")
        return
    if res.returncode == 0:
        print(f"raise: OK — Accessibility is granted for {proc}")
        return
    print(f"raise: BLOCKED — grant Accessibility to {proc} in System Settings "
          "> Privacy & Security > Accessibility, then try again "
          f"({res.stderr.strip().splitlines()[-1] if res.stderr.strip() else 'no detail'})")


def _tabcolor_path(tty):
    return os.path.join(TABCOLOR_DIR, os.path.basename(tty) + ".json")


def tint_start(tty, term, rgb, say_pid, state="speaking"):
    """Tint `tty`, parking what to restore when the tint is done.

    Two kinds of tint share this record. A "speaking" tint lasts one
    readout and is owned by the `say` pid in the record — when that pid
    is gone, the tint is over. A "pending" tint lasts as long as the
    terminal has an unspoken summary and is owned by the queue instead
    (pid is None). Either way restore_stale() can tell whether a tint has
    outlived its reason and put the terminal back.
    """
    if not tty or not rgb or not term:
        return False
    saved = _read_restore(tty)
    # Already tinted (stale flash): keep the ORIGINAL color, not the tint.
    original = saved.get("restore") if saved else (
        _terminal_bg(tty) if term == "Apple_Terminal" else None)
    if term == "Apple_Terminal":
        if original is None:
            return False
        if tuple(original) in TINTS:
            # Reading back a tint as the "original" would bake it in
            # permanently. Refuse rather than strand the tab.
            return False
    if not _paint(tty, term, rgb):
        return False
    try:
        os.makedirs(TABCOLOR_DIR, exist_ok=True)
        with open(_tabcolor_path(tty), "w") as f:
            json.dump({"term": term, "restore": original, "pid": say_pid,
                       "state": state, "shown": list(rgb)}, f)
    except OSError:
        pass
    return True


def _read_restore(tty):
    try:
        with open(_tabcolor_path(tty)) as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def tint_stop(tty):
    """Put `tty` back to the color it had before the readout.

    The record is dropped ONLY once the repaint is confirmed. A repaint
    can fail transiently (osascript timeout, Terminal mid-redraw), and
    dropping the record on a failed repaint is what strands a tab red
    forever — nothing left to say what color it should have been.
    """
    rec = _read_restore(tty)
    if not rec:
        return True
    restore = rec.get("restore")
    for attempt in range(3):
        if _paint(tty, rec.get("term"), tuple(restore) if restore else None):
            break
        time.sleep(0.4 * (attempt + 1))
    else:
        return False  # leave the record; restore_stale() retries later
    try:
        os.remove(_tabcolor_path(tty))
    except OSError:
        pass
    return True


def _ask_path(tty):
    return os.path.join(ASKING_DIR, os.path.basename(tty) + ".json")


def live_asks():
    """{tty: marker} for every terminal with an AskUserQuestion still open.

    A marker whose session process is gone (or that nothing closed for a
    day) is dropped here — that is what stops a killed session from
    leaving a tab purple forever.
    """
    try:
        files = os.listdir(ASKING_DIR)
    except OSError:
        return {}
    out, cutoff = {}, time.time() - ASK_MAX_AGE_SECS
    for fn in files:
        if not fn.endswith(".json"):
            continue
        tty = "/dev/" + fn[:-len(".json")]
        try:
            with open(_ask_path(tty)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            rec = None
        if not isinstance(rec, dict) or rec.get("ts", 0) < cutoff \
                or not _alive(rec.get("owner_pid")) or _ask_dismissed(rec):
            try:
                os.remove(_ask_path(tty))
            except OSError:
                pass
            _silence_ask(rec)
            if (_read_restore(tty) or {}).get("state") == "asking":
                tint_stop(tty)
            continue
        out[tty] = rec
    return out


def _ask_dismissed(rec):
    """True when the session moved on from this question without answering.

    A question dismissed by typing over it (or interrupted) fires no
    PostToolUse, and the Stop-hook clear only comes when the turn ends —
    which for a long agent turn can be an hour of purple and of click-ins
    re-reading a dead question. While a question is genuinely open the
    session is BLOCKED on it, so its transcript sits still; the transcript
    growing past the marker (plus a flush grace) means the turn moved on.
    (A background task appending mid-question can trip this early — that
    costs the purple cue, the cheap side of the trade.)
    """
    transcript = rec.get("transcript") if isinstance(rec, dict) else None
    if not transcript:
        return False  # older marker, no transcript recorded: age/pid only
    try:
        return os.path.getmtime(transcript) > rec.get("ts", 0) + ASK_STALE_GRACE_SECS
    except OSError:
        return False


def _silence_ask(rec):
    """Stop a `say` still reading this question aloud. Closing the question
    is the one unambiguous "I have it" — the voice should stop with it.
    Returns True when it actually cut a live readout short."""
    pid = rec.get("say_pid") if isinstance(rec, dict) else None
    if still_speaking(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            return True
        except OSError:
            pass
    return False


def _unhear_cut(tty, rec):
    """A readout was cut mid-sentence: take back the heard-claim on the
    question it was reading, so coming back to that question reads it
    again — three words of a question is not a heard question. A readout
    that finished naturally keeps its claim, so revisiting a fully-read
    question stays silent."""
    idx = rec.get("saying") if isinstance(rec, dict) else None
    if not isinstance(idx, int) or isinstance(idx, bool):
        return
    try:
        with open(_ask_path(tty)) as f:
            latest = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(latest, dict) or latest.get("ts") != rec.get("ts"):
        return
    counts = latest.get("heard") or {}
    cur = int(counts.get(str(idx)) or 0)
    if not cur:
        return
    counts[str(idx)] = cur - 1
    latest["heard"] = counts
    try:
        with open(_ask_path(tty), "w") as f:
            json.dump(latest, f)
    except OSError:
        pass


def _mark_ask_say(tty, ts, pid):
    """Record the `say` reading a question in its marker, so whatever
    closes the question can also silence it. Left alone if the marker
    has been replaced by a newer question in the meantime."""
    try:
        with open(_ask_path(tty)) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return
    if isinstance(rec, dict) and rec.get("ts") == ts:
        rec["say_pid"] = pid
        try:
            with open(_ask_path(tty), "w") as f:
                json.dump(rec, f)
        except OSError:
            pass


def _alive(pid):
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def ask_open(payload):
    """An AskUserQuestion just opened: mark the terminal as blocked on you.

    Tinting and speaking happen only if that tab is NOT the one you are
    looking at — if it is, the question is already in front of you. The
    marker is written either way, so switching away later still turns the
    tab purple (the watcher owns that transition).

    This runs as a PreToolUse hook, which BLOCKS the question from
    rendering until it returns: it does the cheap part (write the marker)
    and hands the AppleScript — focus check, tint, speech — to a detached
    child, so the prompt is never held up by a terminal round-trip.
    """
    tty, owner_pid = owning_tty_pid()
    if not tty:
        return
    session_id = payload.get("session_id") or ""
    tool_input = payload.get("tool_input") or {}
    questions = tool_input.get("questions") or []
    headers = [str(q.get("header") or "").strip()
               for q in questions if isinstance(q, dict)]
    # Headers carry the unfocused first-open cue; the full text carries the
    # click-in read-out. Option LABELS only — the descriptions are
    # elaboration you can read on screen, and speaking them would double
    # the length of an already long readout.
    asked = []
    for q in questions[:4]:
        if not isinstance(q, dict):
            continue
        options = [str(o.get("label") or "").strip()
                   for o in (q.get("options") or []) if isinstance(o, dict)]
        asked.append({"q": str(q.get("question") or "").strip(),
                      "options": [o for o in options if o][:4]})
    project = os.path.basename(payload.get("cwd") or "") or "unknown"
    title = session_name(session_id)
    rec = {"tty": tty, "term": os.environ.get("TERM_PROGRAM") or "",
           "session": session_id, "ts": int(time.time()),
           "transcript": payload.get("transcript_path") or "",
           "owner_pid": owner_pid, "project": project,
           "headers": [h for h in headers if h],
           "questions": [a for a in asked if a["q"]],
           "name": humanize(title) if title else speak_name(project)}
    try:
        os.makedirs(ASKING_DIR, exist_ok=True)
        with open(_ask_path(tty), "w") as f:
            json.dump(rec, f)
    except OSError:
        return
    subprocess.Popen([sys.executable, os.path.abspath(__file__),
                      "--ask-announce", tty],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    spawn_watch()  # owns the focus transitions from here on


def _ask_preamble(transcript, first_q):
    """Text of the assistant message holding this ask's tool_use, or None
    while that message has not reached the transcript yet ("" once it is
    there but carries no text). Claude Code writes each content block as
    its OWN transcript entry, so the text and the tool_use never share
    one — they share a message id, and the text entries land first.
    Matched on the first question's text, so an older ask in the same
    transcript can never be mistaken for this one — the newest match wins.
    """
    texts = {}  # message id -> its text blocks, in transcript order
    found = None
    try:
        with open(transcript, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                message = entry.get("message") or {}
                msg_id = message.get("id")
                for b in message.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text"):
                        texts.setdefault(msg_id, []).append(b["text"])
                    elif b.get("type") == "tool_use" \
                            and b.get("name") == "AskUserQuestion" \
                            and str(((b.get("input") or {}).get("questions")
                                     or [{}])[0].get("question") or "") \
                            == first_q:
                        found = "\n".join(texts.get(msg_id) or [])
    except OSError:
        return None
    return found


def _ask_context(rec):
    """The words Claude wrote right before asking — the WHY of the
    question, which the question text alone rarely carries. Sanitized and
    capped exactly like a spoken summary (`/tts length` honored, adaptive
    otherwise). Waits briefly for the assistant entry to flush: the
    PreToolUse hook can beat Claude Code's transcript write by a beat.
    Empty when the ask arrived with no preamble, or `ask_context` is off.
    """
    session_id = rec.get("session")
    if resolve_setting(session_id, "ask_context", ("on", "off"), "on") == "off":
        return ""
    transcript = rec.get("transcript") or ""
    questions = rec.get("questions") or []
    first_q = str((questions[0] if questions else {}).get("q") or "")
    if not transcript or not first_q:
        return ""
    text = None
    for _ in range(20):  # up to ~5s for the flush
        text = _ask_preamble(transcript, first_q)
        if text is not None:
            break
        time.sleep(0.25)
    if not text:
        return ""
    return _trim(sanitize(text), resolve_summary_cap(session_id, text))


def ask_announce(tty):
    """Detached: first paint + read-out for a question that just opened,
    and the context harvest that later reads lean on.

    Focused tab = you are already looking at the question: no tint, and
    speech only under `ask_first` — hearing question one the moment it
    opens is opt-in, because a question that opens under your nose is
    already on screen. What makes the opt-in legitimate where a summary
    equivalent would not be: a missed summary has `rr`; a missed question
    has nothing. Not gated by `hold` (that governs terminals you are NOT
    watching) nor focus_speak (no click happened) — ask_speak and the mode
    still apply, via speak_ask_question's caller here.

    Everything after this first moment is the watcher's job.
    """
    rec = live_asks().get(tty)
    if not rec:
        return
    focused = focused_tty() == tty
    if not focused:
        ask_paint(tty, rec)
        speak_ask(rec)
    # Harvest the context AFTER the unfocused announce (the headers must
    # not wait on a transcript flush) but BEFORE any ask_first read (its
    # whole point is the why). Stored in the marker so the watcher's
    # click-in and screen-follow reads get it too.
    context = _ask_context(rec)
    if context:
        try:
            with open(_ask_path(tty)) as f:
                latest = json.load(f)
        except (OSError, ValueError):
            latest = None
        if isinstance(latest, dict) and latest.get("ts") == rec.get("ts"):
            latest["context"] = context
            try:
                with open(_ask_path(tty), "w") as f:
                    json.dump(latest, f)
            except OSError:
                pass
            rec = latest
    session_id = rec.get("session")
    if focused \
            and resolve_setting(session_id, "ask_first",
                                ("on", "off"), "off") == "on" \
            and resolve_mode(session_id) != "off" \
            and resolve_setting(session_id, "ask_speak",
                                ("on", "off"), "on") == "on" \
            and not on_call() and active_say_pid() is None \
            and focused_tty() == tty:
        speak_ask_question(tty, rec, 0)


def ask_paint(tty, rec):
    rgb = resolve_ask_color(rec.get("session"))
    if not rgb:
        return
    cur = _read_restore(tty) or {}
    if cur.get("state") == "speaking" and still_speaking(cur.get("pid")):
        return  # a live readout owns the tab; the marker outlives it
    if cur.get("shown") == list(rgb):
        return  # already purple — every repaint is an osascript call
    tint_start(tty, rec.get("term") or os.environ.get("TERM_PROGRAM") or "",
               rgb, None, state="asking")


def speak_ask(rec):
    """Read the question's headers out — enough to know what is being asked
    without going to look. Silent on a call, silent if the voice is already
    busy (a question is not worth talking over a readout), silent if the
    session has TTS or ask_speak off, and silent under `hold`."""
    session_id = rec.get("session")
    if resolve_mode(session_id) == "off":
        return
    if resolve_collision(session_id) == "hold":
        # `hold` means nothing speaks at a terminal you are not watching,
        # and the caller already established you are not watching this one.
        # A question is not an exception to that — it is the loudest kind of
        # unprompted speech there is. The purple tab and the menu bar carry
        # it instead, and it stays yours to walk over to.
        return
    if resolve_setting(session_id, "ask_speak", ("on", "off"), "on") == "off":
        return
    if on_call() or active_say_pid() is not None:
        return
    headers = rec.get("headers") or []
    what = ", ".join(humanize(h) for h in headers[:4])
    text = f"{rec.get('name') or 'Claude'}: waiting on your call"
    text += f". {what}." if what else "."
    proc = subprocess.Popen(say_argv(session_id, text),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass
    _mark_ask_say(rec.get("tty"), rec.get("ts"), proc.pid)


def _visible_text(tty, term):
    """Visible text of the focused tab, or None if the emulator cannot be
    asked (or the selected tab turns out not to be `tty` — the answer is
    only trusted when it names the terminal it came from, so a focus
    switch between the poll's focus check and this call reads as
    "cannot tell", never as another tab's text)."""
    if term == "Apple_Terminal":
        out = _osascript('tell application "Terminal"\nif frontmost then\n'
                         'return (tty of selected tab of front window) & '
                         '"\\n" & (contents of selected tab of front window)\n'
                         'end if\nend tell\nreturn ""')
    elif term == "iTerm.app":
        out = _osascript('tell application "iTerm2"\nif frontmost then\n'
                         'return (tty of current session of current window) & '
                         '"\\n" & (text of current session of current window)\n'
                         'end if\nend tell\nreturn ""')
    else:
        return None
    if not out:
        return None
    head, _, body = out.partition("\n")
    return body if head.strip() == tty else None


def _ask_norm(s):
    """Alphanumerics only, single-spaced. The TUI wraps question text and
    boxes it in border glyphs, so matching on anything richer would break
    the needle at every wrapped line."""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def displayed_question(tty, rec):
    """Index of the sub-question currently rendered in the tab, or None
    when the screen cannot say. Highest matching index wins: the TUI only
    renders a question once you reach it, so when an already-answered
    question's text is still visible above, the newest match is the one
    you are on."""
    text = _visible_text(tty, rec.get("term") or "")
    if not text:
        return None
    hay = _ask_norm(text)
    found = None
    for i, q in enumerate(rec.get("questions") or []):
        if not isinstance(q, dict):
            continue
        needle = _ask_norm(str(q.get("q") or ""))[:ASK_NEEDLE_CHARS]
        if len(needle) >= ASK_NEEDLE_MIN and needle in hay:
            found = i
    return found


def speak_ask_question(tty, rec, idx):
    """Speak sub-question `idx` — text plus option LABELS (the labels are
    the answers you are choosing between; the descriptions are on screen)
    — and log it as heard in the marker. The name intro and the "plus two
    more" count ride only the set's first read; later questions lead with
    their number, so advancing sounds like advancing, not like a new
    session piping up.

    The heard-claim is written before the speech starts. The watcher is
    the only writer of this field, so no locking — but re-read anyway and
    leave it alone if the marker has been replaced, so a question that
    opened in the last instant does not start life part-heard.
    """
    questions = rec.get("questions") or []
    if not (0 <= idx < len(questions)) or not isinstance(questions[idx], dict):
        return
    text = str(questions[idx].get("q") or "").strip()
    if not text:
        return
    if text[-1] not in ".?!":
        text += "."
    options = [str(o).strip() for o in (questions[idx].get("options") or [])
               if str(o).strip()]
    if options:
        text += " Options: " + "; ".join(options) + "."
    heard = rec.get("heard") or {}
    if any(heard.values()):
        lead = f"Question {idx + 1}. "
    else:
        lead = f"{rec.get('name') or 'Claude'} is asking. "
        # The why before the what: the words Claude wrote just before
        # asking, harvested by ask_announce (empty under ask_context off,
        # or when the ask came with no preamble).
        context = str(rec.get("context") or "").strip()
        if context:
            if context[-1] not in ".?!":
                context += "."
            lead += context + " The question: "
        more = len(questions) - idx - 1
        if more:
            text += f" Plus {more} more question{'s' if more > 1 else ''}."
    try:
        with open(_ask_path(tty)) as f:
            latest = json.load(f)
    except (OSError, ValueError):
        latest = None
    if isinstance(latest, dict) and latest.get("ts") == rec.get("ts"):
        counts = latest.get("heard") or {}
        counts[str(idx)] = int(counts.get(str(idx)) or 0) + 1
        latest["heard"] = counts
        latest["saying"] = idx
        try:
            with open(_ask_path(tty), "w") as f:
                json.dump(latest, f)
        except OSError:
            pass
    proc = subprocess.Popen(say_argv(rec.get("session"), lead + text),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass
    _mark_ask_say(tty, rec.get("ts"), proc.pid)


def _ask_gates_open(session_id):
    """The speech gates shared by every focused question read: TTS on,
    ask_speak on, focus_speak on ("no click-to-talk" covers questions as
    much as summaries). Deliberately does NOT include `hold`, unlike
    speak_ask(): that setting governs unprompted speech at a terminal you
    are not watching, and a read at the focused tab is the opposite of
    unprompted — the same reasoning that leaves speak_on_focus() ungated.
    """
    if resolve_mode(session_id) == "off":
        return False
    if resolve_setting(session_id, "ask_speak", ("on", "off"), "on") == "off":
        return False
    return resolve_setting(session_id, "focus_speak",
                           ("on", "off"), "on") == "on"


def _heard_count(rec, idx):
    return int((rec.get("heard") or {}).get(str(idx)) or 0)


def ask_screen_follow(tty, rec, owed):
    """ask_follow `screen`, and you are sitting on a tab with an open
    question: keep the voice on the sub-question actually displayed.

    Each question is read the FIRST time it appears on screen — advancing
    to it (Enter, arrow keys, a click, however) is you asking for the next
    one. The set's first question is the exception: it only reads on a
    click-IN (`owed`), because a question that opens under your nose is
    already in front of you — the rule that keeps every other unprompted
    readout away from the focused tab. Moving on mid-readout cuts the
    voice: the screen no longer showing a question is the end of anyone's
    interest in hearing it.

    Returns True when the screen answered "which question is up" — the
    click-cursor fallback must then stay quiet even if nothing was spoken
    — and False when it could not tell (unscrapable emulator, scrape
    miss), which hands the click-in to speak_ask_on_focus().
    """
    if not _ask_gates_open(rec.get("session")):
        return False
    idx = displayed_question(tty, rec)
    if idx is None:
        return False
    # A set is "engaged" once any question read has happened — from then
    # on, returning to an unheard question (a cut-off question one
    # included) reads it without needing a fresh click-in. Before any
    # read, question one still waits for the click: a set that opens
    # under your nose is already on screen.
    engaged = isinstance(rec.get("saying"), int)
    if rec.get("saying") != idx and _silence_ask(rec):
        # You moved past a question mid-readout: cut the voice, and take
        # the heard-claim back so arrowing back to it reads it again.
        _unhear_cut(tty, rec)
    if _heard_count(rec, idx) == 0 and (idx > 0 or owed or engaged):
        if active_say_pid() is not None:
            # The voice is mid-something else; a later poll retries. An
            # owed first read survives by returning False — the watcher
            # keeps the owed flag, and the cursor fallback is voice-gated
            # so it stays quiet too. A later question needs no owed flag.
            return not owed
        speak_ask_question(tty, rec, idx)
    return True


def speak_ask_on_focus(tty, rec):
    """You clicked into a terminal holding an open question and the screen
    could not say which sub-question you are on (ask_follow `click`, an
    emulator without AppleScript text access, a scrape miss): read from
    the cursor instead.

    The one state that means YOU are the blocker was also the only one you
    could not hear on demand: speak_ask() fires once, unfocused, and reads
    headers only, so clicking in just dropped the tint and said nothing.
    This is the click-to-talk that summaries already have. Each click-in
    reads the next UNHEARD question — click in, hear one, switch away (the
    readout stops), click back, hear the next — because the UI shows one
    question at a time and without the screen a hook cannot see you
    advance; reading them all at once just talks over questions you are
    not on yet. Once every question is heard, further click-ins re-cycle
    only while `ask_reads` (default 1) allows: past that the questions
    have been in front of you for a while and repeating them is nagging.
    The purple tab still returns on every switch away, because that
    reflects the question being open, not unheard. Under ask_follow `off`
    it is the first question only, `ask_reads` times, no advancement.
    """
    session_id = rec.get("session")
    if not _ask_gates_open(session_id):
        return
    if on_call() or active_say_pid() is not None:
        return
    questions = rec.get("questions") or []
    if not questions:
        return  # an older marker, written before questions were stored
    cap = resolve_ask_reads(session_id)
    if resolve_ask_follow(session_id) == "off":
        idx = 0 if _heard_count(rec, 0) < cap else None
    else:
        idx = next((i for i in range(len(questions))
                    if _heard_count(rec, i) == 0), None)
        if idx is None:
            idx = next((i for i in range(len(questions))
                        if _heard_count(rec, i) < cap), None)
    if idx is not None:
        speak_ask_question(tty, rec, idx)


def ask_clear(tty):
    """The question is answered (or the turn ended): drop marker and tint,
    and stop any readout of the question still mid-sentence — answering
    from the ask UI is the way to shut it up."""
    if not tty:
        return
    try:
        with open(_ask_path(tty)) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        rec = None
    try:
        os.remove(_ask_path(tty))
    except OSError:
        pass
    _silence_ask(rec)
    if (_read_restore(tty) or {}).get("state") == "asking":
        tint_stop(tty)


def _asking_tints():
    """Every tty whose current tint record says "asking"."""
    try:
        files = os.listdir(TABCOLOR_DIR)
    except OSError:
        return []
    ttys = []
    for fn in files:
        if fn.endswith(".json"):
            tty = "/dev/" + fn[:-len(".json")]
            if (_read_restore(tty) or {}).get("state") == "asking":
                ttys.append(tty)
    return ttys


def still_speaking(pid):
    """True while `pid` is a live `say` — pids get reused, so check the name."""
    if not isinstance(pid, int):
        return False
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        return False
    return out.endswith("say")


def restore_stale():
    """Un-tint terminals whose tint has outlived its reason.

    A speaking tint is over when its `say` pid is gone; a pending tint is
    over when the terminal has nothing unspoken left in the queue; an
    asking tint is over when the question is closed or the session that
    asked it is gone. Either way the terminal goes back to its own color,
    so no crash or kill can leave one stuck.
    """
    try:
        files = os.listdir(TABCOLOR_DIR)
    except OSError:
        return
    waiting = pending_by_tty()
    asking = live_asks()
    for fn in files:
        if not fn.endswith(".json"):
            continue
        tty = "/dev/" + fn[:-len(".json")]
        rec = _read_restore(tty) or {}
        if rec.get("state") == "asking":
            if tty not in asking:
                tint_stop(tty)
        elif rec.get("state") == "pending":
            if not waiting.get(tty):
                tint_stop(tty)
        elif not still_speaking(rec.get("pid")):
            tint_stop(tty)


def load_queue():
    """Every readable entry. Parsed PER LINE on purpose: two hooks can
    append concurrently and a large `full`-mode summary can exceed the
    stream buffer, so a torn line is possible. Failing the whole file on
    one bad line would make every waiting summary vanish at once — and
    save_queue() would then write that empty list back, destroying the
    real backlog. Skip the bad line, keep the rest (same as tts-recap.py).
    """
    entries = []
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            for ln in f:
                if not ln.strip():
                    continue
                try:
                    entries.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return entries


def save_queue(entries):
    try:
        tmp = QUEUE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, QUEUE_FILE)
        return True
    except OSError:
        return False


def pending_by_tty(entries=None):
    """{tty: [unspoken entries, oldest first]} — what each terminal owes you."""
    cutoff = time.time() - WATCH_MAX_AGE_SECS
    waiting = {}
    for e in (load_queue() if entries is None else entries):
        if e.get("spoken") or not e.get("tty") or e.get("ts", 0) < cutoff:
            continue
        waiting.setdefault(e["tty"], []).append(e)
    return waiting


def wait_color(age_secs):
    """green → yellow → red as an unspoken summary ages."""
    name = WAIT_STAGES[0][1]
    for threshold, color in WAIT_STAGES:
        if age_secs >= threshold:
            name = color
    return parse_color(name)


def focused_tty():
    """tty of the tab you are actually looking at, or None.

    Asks the terminal app directly (its own `frontmost` property), so
    this needs no Accessibility permission — unlike raising a window.
    """
    term = os.environ.get("TERM_PROGRAM") or ""
    if term == "Apple_Terminal":
        out = _osascript('tell application "Terminal"\nif frontmost then\n'
                         "return tty of selected tab of front window\n"
                         'end if\nend tell\nreturn ""')
    elif term == "iTerm.app":
        out = _osascript('tell application "iTerm2"\nif frontmost then\n'
                         "return tty of current session of current window\n"
                         'end if\nend tell\nreturn ""')
    else:
        return None
    return out or None


def watch():
    """Age the pending tints and read a terminal out when you look at it.

    Also owns the purple ask cue: a terminal with an open question is
    purple whenever it is in the BACKGROUND and its own color whenever you
    are looking at it, so the tint follows focus for as long as the
    question stays open.

    One instance at a time (flock). Runs only while something is unspoken
    or a question is open, and exits as soon as both are clear, so nothing
    is polling in the background during normal use.
    """
    # Retry rather than exit on contention. The outgoing watcher holds the
    # lock through its own teardown (restore_stale() repaints, each an
    # osascript with its own timeout), and a summary enqueued in exactly
    # that window would otherwise find the lock held, exit here, and be
    # left with no watcher at all: no tint, no aging, no read on focus,
    # until some later Stop hook happened to spawn one. Waiting a few
    # seconds costs a doomed child a few seconds; not waiting costs a
    # summary its entire delivery.
    for attempt in range(12):
        try:
            lock = open(WATCH_LOCK, "w")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:  # BlockingIOError is an OSError
            time.sleep(0.5)
    else:
        return  # a watcher really is running; it will pick this up
    term = os.environ.get("TERM_PROGRAM") or ""
    # A read is owed only when you CLICK INTO a waiting terminal — a
    # transition, not a state. Seeding last_focus with wherever you
    # already are means the terminal you happen to be sitting in does not
    # start talking at you the moment a summary lands in it.
    last_focus = focused_tty()
    owed = None
    ask_owed = None
    badge_cleared = False
    while True:
        entries = load_queue()
        waiting = pending_by_tty(entries)
        asking = live_asks()
        if not waiting and not asking:
            restore_stale()  # clear any leftover pending/asking tints
            # restore_stale() can take seconds (an osascript repaint per
            # stranded tab), and the lock is still held throughout. Look
            # once more before letting go: anything that arrived during
            # teardown belongs to THIS watcher, because the hook that
            # enqueued it already tried to spawn a replacement and found
            # the lock held. Closing the window from both sides.
            if pending_by_tty() or live_asks():
                continue
            if active_say_pid() is None:
                badge(None)
            return
        now = time.time()
        focus = focused_tty()
        if active_say_pid() is None:  # a live readout owns the badge instead
            if waiting:
                oldest = min(e.get("ts", now) for q in waiting.values() for e in q)
                # Default matters: a clock step backwards (sleep/wake, NTP)
                # makes `now - oldest` negative, matching no stage. Without
                # a fallback that raises StopIteration out of watch(), and
                # the `finally: sys.exit(0)` masks it as a clean exit — the
                # watcher just dies and tints stop aging. wait_color() has
                # always defaulted to green here; match it.
                stage = next((c for t, c in reversed(WAIT_STAGES)
                              if now - oldest >= t), WAIT_STAGES[0][1])
                n = sum(len(q) for q in waiting.values())
                badge(f"{STAGE_EMOJI[stage]} {n} waiting")
                badge_cleared = False
            elif not badge_cleared:
                # Only questions left — nothing is "waiting to be read".
                badge(None)
                badge_cleared = True
        # Open questions first: purple while backgrounded, dropped the
        # moment you look at the tab. The marker survives either way, so
        # switching back and forth just repaints.
        for tty, rec in asking.items():
            if tty == focus:
                if (_read_restore(tty) or {}).get("state") == "asking":
                    tint_stop(tty)
            else:
                ask_paint(tty, rec)
        # A tab can be left purple with no live question behind it: an
        # ask_clear (or a stale-marker drop) can land between this loop's
        # live_asks() snapshot and its repaint, painting the answered
        # question right back. The loops here never touch such a tab and
        # restore_stale() only runs once EVERYTHING is clear — so while
        # other work keeps the watcher alive, the purple would sit for
        # hours. Sweep it every poll instead.
        for tty in _asking_tints():
            if tty not in asking:
                tint_stop(tty)
        for tty, queued in waiting.items():
            if tty in asking:
                continue  # a live question outranks a summary on the same tab
            rgb = wait_color(now - min(e.get("ts", now) for e in queued))
            rec = _read_restore(tty) or {}
            # Never repaint over a live readout, and don't repaint a color
            # that is already showing (each repaint is an osascript call).
            if rec.get("state") == "speaking" and still_speaking(rec.get("pid")):
                continue
            if rec.get("shown") != list(rgb):
                tint_start(tty, queued[0].get("term") or term, rgb, None,
                           state="pending")
        if focus != last_focus:
            # You just switched terminals. If you landed on one that is
            # holding something, it owes you a read — at ANY age. Clicking
            # a waiting terminal is an explicit ask, and a summary that is
            # still colored is a summary you have not heard; refusing to
            # read it because it has been sitting a while just leaves a
            # colored tab that nothing will ever clear. (An age cutoff was
            # tried here and was wrong: real summaries routinely wait far
            # longer than a few minutes before you get back to them.)
            owed = focus if focus in waiting else None
            # An open question owes you a read on the same terms. This has
            # to hang off the TRANSITION, not the asking loop above, which
            # runs every poll — put it there and the question re-reads
            # itself every 1.5 seconds for as long as it stays open.
            ask_owed = focus if focus in asking else None
            # Leaving a question mid-readout is the stop gesture: you have
            # walked away, so it stops talking — and the cut question is
            # un-heard, so coming back reads it again. Keyed to the tab you
            # LEFT, never the unfocused first-open announce — that one
            # plays at a background tab by design and must survive focus
            # churn.
            left = asking.get(last_focus)
            if left and _silence_ask(left):
                _unhear_cut(last_focus, left)
            last_focus = focus
        if owed and owed in waiting and active_say_pid() is None and not on_call():
            ready = [e for e in waiting[owed]
                     if resolve_setting(e.get("session"), "focus_speak",
                                        ("on", "off"), "on") == "on"]
            if ready:
                speak_on_focus(owed, ready)
            owed = None  # paid up: it stays quiet until you come back again
        # ask_follow `screen`: while you sit on a tab with an open
        # question, follow the sub-question on screen — one scrape per
        # poll, ONLY in this state, so nothing reads the screen at any
        # other time. When the screen answers, it owns the read and the
        # click-cursor below stays quiet; when it cannot tell, a click-in
        # falls through to the cursor.
        cur = asking.get(focus)
        if cur and not on_call() \
                and resolve_ask_follow(cur.get("session")) == "screen" \
                and ask_screen_follow(focus, cur, ask_owed == focus):
            ask_owed = None
        if ask_owed and ask_owed in asking and active_say_pid() is None \
                and not on_call():
            speak_ask_on_focus(ask_owed, asking[ask_owed])
            ask_owed = None
        time.sleep(WATCH_POLL_SECS)


def speak_on_focus(tty, queued):
    """You looked at the terminal — read it what it has been holding.

    Only the newest `recap_max` are spoken, oldest-first inside that window
    so the final words you hear are the current state. Anything older is
    counted out loud up front ("8 older updates skipped") rather than read
    — a long session's backlog is mostly stale by the time you get back to
    it. Skipped entries are still marked played along with the rest, so the
    tab clears in one click instead of handing you the same wall next time;
    `repeat 10` / `repeat 30m` ignore the played flag and are the way back
    to them.
    """
    # Resolved against the NEWEST entry — that is the session you clicked
    # into, and the one whose per-session setting should decide.
    newest_session = queued[-1].get("session")
    n = resolve_recap_max(newest_session)
    window = queued[-n:]
    text = " ... Next. ".join(
        f"{e.get('name') or speak_name(e.get('project') or '')}: {e.get('text')}"
        for e in window)
    skipped = len(queued) - len(window)
    if skipped:
        text = (f"{skipped} older update{'' if skipped == 1 else 's'} skipped. "
                + text)
    stamps = {(e.get("ts"), e.get("text")) for e in queued}
    entries = load_queue()
    for e in entries:  # claim before speaking, so nobody doubles up
        if (e.get("ts"), e.get("text")) in stamps:
            e["spoken"] = True
    if not save_queue(entries):
        return
    tint_stop(tty)
    badge("🔊 " + (window[0].get("name") or ""), window[0].get("session"))
    proc = subprocess.Popen(say_argv(newest_session, text),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass
    rgb = resolve_tab_color(window[0].get("session"))
    if tint_start(tty, window[0].get("term"), rgb, proc.pid):
        spawn_untinter(tty)


def badge(text, session_id=None):
    """Show `text` in the menu bar, or clear it with None.

    The state visible in the top bar even when every terminal is buried:
    which one is speaking, or how many summaries are waiting. Writing the
    file IS the API — the compiled helper mirrors it and quits when the
    file goes away, so there is no daemon to manage.
    """
    if resolve_setting(session_id, "menubar", ("on", "off"), "on") == "off":
        return
    if text is None:
        try:
            os.remove(BADGE_FILE)
        except OSError:
            pass
        return
    if not os.access(BADGE_HELPER, os.X_OK):
        return  # not built (no swiftc at install time) — nothing to show it
    try:
        with open(BADGE_FILE, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        return
    try:
        running = subprocess.run(["pgrep", "-f", "speaking-badge"],
                                 capture_output=True).stdout.strip()
        if not running:
            subprocess.Popen([BADGE_HELPER], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


CHIME_SOUND = "/System/Library/Sounds/Glass.aiff"


def chime(after_pid=None, session_id=None):
    """A soft "something arrived" tone. `/tts chime off` silences it.

    Never plays while the mic or camera is live — a chime in a meeting is
    worse than a missed summary. A deferred chime (one waiting out a
    readout in progress so it doesn't mask it) re-checks that at the
    moment it would actually play, not just when it was scheduled: a call
    can easily start during the wait.
    """
    if resolve_setting(session_id, "chime", ("on", "off"), "on") == "off":
        return
    if on_call():
        return
    if after_pid:
        subprocess.Popen([sys.executable, os.path.abspath(__file__),
                          "--chime", "--after", str(after_pid)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return
    play_chime()


def play_chime(after_pid=None):
    """Play the tone, optionally after `after_pid` finishes speaking.
    Silent if a call has started by the time the wait is over."""
    deadline = time.time() + 900
    while after_pid and time.time() < deadline:
        if not still_speaking(after_pid):
            break
        time.sleep(0.3)
    if on_call():
        return
    try:
        subprocess.Popen(["/usr/bin/afplay", "-v", "0.5", CHIME_SOUND],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def notify(title, text, session_id=None):
    """Banner for a summary that could NOT be spoken — you were on a call,
    or another terminal was talking. Never for one you just heard, and
    never focus-stealing: a banner cannot take the keyboard."""
    if resolve_setting(session_id, "notify", ("on", "off"), "on") == "off":
        return
    body = text if len(text) <= 200 else text[:197].rsplit(" ", 1)[0] + "…"
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    _osascript(f'display notification "{esc(body)}" with title '
               f'"{esc(title)}" subtitle "waiting — focus the terminal to hear it"')


def spawn_watch():
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--watch"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def spawn_untinter(tty):
    """Detached: hold the tint until the voice stops, then restore."""
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--untint", tty],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def untint(tty):
    deadline = time.time() + 900
    while time.time() < deadline:
        rec = _read_restore(tty)
        if not rec:
            return  # someone else already restored it
        if not still_speaking(rec.get("pid")):
            break
        time.sleep(0.3)
    tint_stop(tty)
    # The readout is over: drop the menu-bar badge, unless summaries are
    # still waiting elsewhere — then the watcher owns it and repaints.
    if not pending_by_tty():
        badge(None)


def spawn_drainer():
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--drain"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def drain():
    """Speak queued entries in order, one per voice-idle gap (follow mode).

    Exactly one drainer runs at a time (flock): collisions during a
    readout just enqueue and their drainer exits — the live one loops
    until the queue is empty, so it picks those entries up. Only speaks
    entries TAGGED follow (enqueued by a follow-mode session) and recent
    (FOLLOW_WINDOW_SECS) — chime-mode entries, call-held entries, and
    stale backlog stay parked for /spoken-recap. A call starting
    mid-readout stops the drainer.
    """
    def speakable(e):
        return (not e.get("spoken") and e.get("follow")
                and e.get("held") != "call"
                and e.get("ts", 0) >= time.time() - FOLLOW_WINDOW_SECS)

    try:
        lock = open(DRAIN_LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    while True:
        if active_say_pid() is not None:
            time.sleep(0.3)
            continue
        if on_call():
            return
        # Load the queue and claim the oldest speakable entry. Marked
        # spoken BEFORE the readout so the rewrite window (a concurrent
        # append between our read and replace would be lost) stays tiny.
        try:
            with open(QUEUE_FILE, encoding="utf-8") as f:
                entries = [json.loads(ln) for ln in f if ln.strip()]
        except (OSError, ValueError):
            return
        entry = next((e for e in entries if speakable(e)), None)
        if entry is None:
            time.sleep(0.7)  # grace: catch an entry landing right now
            try:
                with open(QUEUE_FILE, encoding="utf-8") as f:
                    if any(speakable(json.loads(ln))
                           for ln in f if ln.strip()):
                        continue
            except (OSError, ValueError):
                pass
            return
        entry["spoken"] = True
        try:
            tmp = QUEUE_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            os.replace(tmp, QUEUE_FILE)
        except OSError:
            return
        prefix = entry.get("name") or speak_name(entry.get("project") or "")
        badge("🔊 " + prefix, entry.get("session"))
        proc = subprocess.Popen(
            say_argv(entry.get("session"), f"{prefix}: {entry.get('text')}"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        try:
            with open(PID_FILE, "w") as f:
                f.write(str(proc.pid))
        except OSError:
            pass
        # Tint the terminal this summary came FROM (not this drainer's own,
        # which belongs to whichever session happened to spawn it).
        tty = entry.get("tty")
        tinted = tint_start(tty, entry.get("term"),
                            parse_color(entry.get("color")), proc.pid)
        if entry.get("raise") == "window":
            raise_window(tty, entry.get("term"))
        proc.wait()
        if tinted:
            tint_stop(tty)
        if not pending_by_tty():
            badge(None)


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    # The turn is over, so any question it asked is closed — clear the ask
    # cue before anything can return early. Belt and braces for a
    # PostToolUse that never fired (question dismissed, session killed).
    ask_clear(owning_tty())
    transcript = payload.get("transcript_path")
    if not transcript or not os.path.exists(transcript):
        return
    session_id = payload.get("session_id") or os.path.splitext(
        os.path.basename(transcript))[0]
    mode = resolve_mode(session_id)
    if mode == "off":
        return
    restore_stale()  # heal any terminal a killed watcher left tinted
    text = fresh_assistant_text(transcript, session_id)
    if not text:
        return
    spoken = pick_speech(text, mode, resolve_summary_cap(session_id, text))
    if not spoken:
        return
    project = os.path.basename(payload.get("cwd") or "") or "unknown"
    rgb = resolve_tab_color(session_id)
    tty = owning_tty()
    term = os.environ.get("TERM_PROGRAM") or ""
    if on_call():
        # Mic or camera is live — Justin is probably on a call. Total
        # silence (even the chime would bleed into a meeting); the summary
        # waits in the queue, colors the tab, and reads out when the
        # terminal is focused (or on /spoken-recap).
        enqueue({"ts": int(time.time()), "project": project,
                 "session": session_id, "text": spoken, "spoken": False,
                 "held": "call", "tty": tty, "term": term,
                 "color": ("#%02x%02x%02x" % rgb) if rgb else "off",
                 "name": humanize(session_name(session_id) or "")
                         or speak_name(project)})
        notify(humanize(session_name(session_id) or "") or speak_name(project),
               spoken, session_id)
        spawn_watch()
        return
    busy_pid = active_say_pid()
    collision = resolve_collision(session_id)
    # Being already there counts as being there. Focus is checked ONCE, here
    # on arrival: if this summary just landed in the tab you are looking at,
    # `hold` speaks it on the spot rather than making you click off and back
    # to hear it. Presence is the prompt. Unfocused is unchanged — it waits
    # and the watcher reads it out on switch-in. Only reachable with the
    # voice free (busy falls through to waiting) and never on a call (that
    # path returned above), and it honours `focus_speak`: turning click-to-
    # talk off turns this off too, since it is the same read.
    focused_here = (
        busy_pid is None and tty is not None and collision == "hold"
        and resolve_setting(session_id, "focus_speak", ("on", "off"), "on") == "on"
        and tty == focused_tty())
    # Resolve the announce-name now, while the session registry entry is
    # alive: /rename (or auto) session title > /tts names map > folder name.
    title = session_name(session_id)
    # The terminal is carried on the entry so a later readout (the
    # follow-mode drainer, or a focus read) tints the terminal the
    # summary CAME from, not whichever one is doing the speaking.
    entry = {"ts": int(time.time()), "project": project,
             "session": session_id, "text": spoken,
             "spoken": busy_pid is None and (collision != "hold" or focused_here),
             "name": humanize(title) if title else speak_name(project),
             "tty": tty, "term": term,
             "color": ("#%02x%02x%02x" % rgb) if rgb else "off",
             "raise": resolve_raise(session_id)}
    if collision == "follow":
        entry["follow"] = True
    enqueue(entry)
    if collision == "hold" and not focused_here:
        # You are not looking at this terminal, so nothing speaks. The tab
        # goes green and a soft chime says "something arrived" — enough to
        # notice when you're deep in something, not enough to pull you out
        # of it. It reads out when you click into the terminal.
        spawn_watch()
        chime(after_pid=busy_pid, session_id=session_id)
        notify(entry["name"], spoken, session_id)
        return
    if busy_pid is not None:
        # Something is already talking: don't collide. Either way the
        # summary is now unspoken and waiting, so the tab starts aging
        # green → yellow → red and reads out when you focus it.
        spawn_watch()
        notify(entry["name"], spoken, session_id)
        if collision == "follow":
            # The summary speaks automatically right after the current
            # speech (and any earlier queued entries) — no chime.
            spawn_drainer()
            return
        # chime (default): chime AFTER the current speech finishes so
        # neither is masked; the summary waits for a click or /spoken-recap.
        chime(after_pid=busy_pid, session_id=session_id)
        return
    # Name-prefixed on every path — immediate, drainer, /spoken-recap — so a
    # summary is always attributable to a terminal, contention or not.
    badge("🔊 " + entry["name"], session_id)
    proc = subprocess.Popen(say_argv(session_id, f"{entry['name']}: {spoken}"),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass
    # Tint this terminal for the readout; a detached watcher restores it
    # when the voice stops, since the hook itself must return now.
    if tint_start(tty, term, rgb, proc.pid):
        spawn_untinter(tty)
    if entry["raise"] == "window":
        raise_window(tty, term)


def _arg(flag):
    """Value following `flag` on the command line, or None."""
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except (ValueError, IndexError):
        return None


if __name__ == "__main__":
    try:
        if "--ask-open" in sys.argv:
            # PreToolUse[AskUserQuestion]: this terminal is now blocked on you.
            try:
                ask_open(json.load(sys.stdin))
            except (json.JSONDecodeError, ValueError):
                pass
        elif "--ask-announce" in sys.argv:
            ask_announce(sys.argv[sys.argv.index("--ask-announce") + 1])
        elif "--ask-close" in sys.argv:
            # PostToolUse[AskUserQuestion]: answered — drop marker and tint.
            ask_clear(owning_tty())
        elif "--drain" in sys.argv:
            drain()
        elif "--untint" in sys.argv:
            untint(sys.argv[sys.argv.index("--untint") + 1])
        elif "--check-raise" in sys.argv:
            check_raise()
        elif "--repair" in sys.argv:
            repair()
        elif "--watch" in sys.argv:
            watch()
        elif "--chime" in sys.argv:
            after = _arg("--after")
            play_chime(int(after) if after and after.isdigit() else None)
        elif "--settings" in sys.argv:
            show_settings(_arg("--session"))
        elif "--set" in sys.argv:
            i = sys.argv.index("--set")
            print(set_setting(sys.argv[i + 1], sys.argv[i + 2],
                              _arg("--session")))
        else:
            main()
    finally:
        sys.exit(0)
