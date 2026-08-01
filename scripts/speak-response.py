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
  hold   - never speak unprompted, even with the voice free: chime, color
           the tab, and wait to be clicked into. For when you are deep in
           something and a voice would break it.
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
  green   - has a summary ready and waiting
  yellow  - has been waiting over 30s
  red     - has been waiting over 5min
Focus a waiting terminal and it reads out on the spot, then goes back to
its own color. The aging and the focus read are done by a single locked
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
import subprocess
import sys
import time

CLAUDE_DIR = os.path.expanduser("~/.claude")
PID_FILE = os.path.join(CLAUDE_DIR, "scripts", ".speak-response.pid")
STATE_FILE = os.path.join(CLAUDE_DIR, "tts-state.json")
SESSION_DIR = os.path.join(CLAUDE_DIR, "tts-sessions")
QUEUE_FILE = os.path.join(CLAUDE_DIR, "tts-queue.jsonl")
AV_HELPER = os.path.join(CLAUDE_DIR, "scripts", "av-status")
DRAIN_LOCK = os.path.join(CLAUDE_DIR, "scripts", ".tts-drain.lock")
TABCOLOR_DIR = os.path.join(CLAUDE_DIR, "tts-tabcolor")
# Speaking tint. Dark enough that white terminal text stays readable.
TAB_COLORS = {"red": "#550000", "orange": "#553300", "yellow": "#4d4d00",
              "green": "#004d1a", "blue": "#00304d", "purple": "#3d0055"}
DEFAULT_TAB_COLOR = "blue"  # while actually speaking
# A summary that could not be spoken yet ages in place: green when it is
# ready to talk, yellow once it has been waiting a while, red once it has
# been waiting a long time. Focus the terminal and it reads out.
WAIT_STAGES = ((0, "green"), (30, "yellow"), (300, "red"))
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


def explicit_summary_cap(session_id):
    """Explicit flat cap from /tts length: per-session "summary_chars" > global.

    A positive number; anything else (missing, zero, non-numeric, bool) is
    ignored and the search falls through. None means "no explicit cap set" —
    the caller then falls back to the adaptive proportional cap.
    """
    paths = []
    if session_id:
        paths.append(os.path.join(SESSION_DIR, f"{session_id}.json"))
    paths.append(STATE_FILE)
    for path in paths:
        try:
            with open(path) as f:
                val = json.load(f).get("summary_chars")
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
    return explicit_summary_cap(session_id) or adaptive_cap(text)


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


def on_call():
    """True if the mic or camera is actively in use (call, recording).

    Asks the compiled av-status helper (scripts/av-status.c) — the same
    signals as the orange/green menu-bar dots, so it covers Zoom, Teams,
    FaceTime, and browser-tab calls alike. Missing helper or any failure
    means "not on a call": speech must degrade to normal, never to silence.
    """
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
    live registry (~/.claude/sessions/<pid>.json), newest entry wins."""
    sess_dir = os.path.expanduser("~/.claude/sessions")
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


def parse_color(raw):
    if raw in ("off", "none", "false"):
        return None
    hexval = TAB_COLORS.get(raw, raw)
    if not re.fullmatch(r"#[0-9a-f]{6}", hexval or ""):
        hexval = TAB_COLORS[DEFAULT_TAB_COLOR]
    return tuple(int(hexval[i:i + 2], 16) for i in (1, 3, 5))


def owning_tty():
    """The controlling terminal of the Claude Code session that fired this
    hook: walk up from this process until a parent has one (the hook itself
    is spawned without one). "/dev/ttysNNN", or None if there is no tty."""
    pid = os.getpid()
    for _ in range(10):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)],
                                 capture_output=True, text=True).stdout.split()
        except OSError:
            return None
        if len(out) != 2:
            return None
        ppid, tty = out
        if tty != "??":
            return tty if tty.startswith("/dev/") else "/dev/" + tty
        try:
            pid = int(ppid)
        except ValueError:
            return None
        if pid <= 1:
            return None
    return None


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
    over when the terminal has nothing unspoken left in the queue. Either
    way the terminal goes back to its own color, so no crash or kill can
    leave one stuck.
    """
    try:
        files = os.listdir(TABCOLOR_DIR)
    except OSError:
        return
    waiting = pending_by_tty()
    for fn in files:
        if not fn.endswith(".json"):
            continue
        tty = "/dev/" + fn[:-len(".json")]
        rec = _read_restore(tty) or {}
        if rec.get("state") == "pending":
            if not waiting.get(tty):
                tint_stop(tty)
        elif not still_speaking(rec.get("pid")):
            tint_stop(tty)


def load_queue():
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            return [json.loads(ln) for ln in f if ln.strip()]
    except (OSError, ValueError):
        return []


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

    One instance at a time (flock). Runs only while something is unspoken
    and exits as soon as the queue is clear, so nothing is polling in the
    background during normal use.
    """
    try:
        lock = open(WATCH_LOCK, "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another watcher already has it
    term = os.environ.get("TERM_PROGRAM") or ""
    # A read is owed only when you CLICK INTO a waiting terminal — a
    # transition, not a state. Seeding last_focus with wherever you
    # already are means the terminal you happen to be sitting in does not
    # start talking at you the moment a summary lands in it.
    last_focus = focused_tty()
    owed = None
    while True:
        entries = load_queue()
        waiting = pending_by_tty(entries)
        if not waiting:
            restore_stale()  # clear any leftover pending tints
            if active_say_pid() is None:
                badge(None)
            return
        now = time.time()
        focus = focused_tty()
        oldest = min(e.get("ts", now) for q in waiting.values() for e in q)
        if active_say_pid() is None:  # a live readout owns the badge instead
            stage = next(c for t, c in reversed(WAIT_STAGES) if now - oldest >= t)
            n = sum(len(q) for q in waiting.values())
            badge(f"{STAGE_EMOJI[stage]} {n} waiting")
        for tty, queued in waiting.items():
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
            # holding something, it owes you a read.
            owed = focus if focus in waiting else None
            last_focus = focus
        if owed and owed in waiting and active_say_pid() is None and not on_call():
            ready = [e for e in waiting[owed]
                     if resolve_setting(e.get("session"), "focus_speak",
                                        ("on", "off"), "on") == "on"]
            if ready:
                speak_on_focus(owed, ready)
            owed = None  # paid up: it stays quiet until you come back again
        time.sleep(WATCH_POLL_SECS)


def speak_on_focus(tty, queued):
    """You looked at the terminal — read it what it has been holding."""
    text = " ... Next. ".join(
        f"{e.get('name') or speak_name(e.get('project') or '')}: {e.get('text')}"
        for e in queued)
    stamps = {(e.get("ts"), e.get("text")) for e in queued}
    entries = load_queue()
    for e in entries:  # claim before speaking, so nobody doubles up
        if (e.get("ts"), e.get("text")) in stamps:
            e["spoken"] = True
    if not save_queue(entries):
        return
    tint_stop(tty)
    badge("🔊 " + (queued[0].get("name") or ""), queued[0].get("session"))
    proc = subprocess.Popen(["/usr/bin/say", text],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass
    rgb = resolve_tab_color(queued[0].get("session"))
    if tint_start(tty, queued[0].get("term"), rgb, proc.pid):
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


def chime(after_pid=None, session_id=None):
    """A soft "something arrived" tone. `/tts chime off` silences it.

    Waits out `after_pid` first when given, so the chime never lands on
    top of a readout already in progress and mask it.
    """
    if resolve_setting(session_id, "chime", ("on", "off"), "on") == "off":
        return
    play = "/usr/bin/afplay -v 0.5 /System/Library/Sounds/Glass.aiff"
    if after_pid:
        play = (f"while kill -0 {after_pid} 2>/dev/null; do sleep 0.3; done; "
                + play)
    try:
        subprocess.Popen(["/bin/sh", "-c", play], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
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
            ["/usr/bin/say", f"{prefix}: {entry.get('text')}"],
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
    # Resolve the announce-name now, while the session registry entry is
    # alive: /rename (or auto) session title > /tts names map > folder name.
    title = session_name(session_id)
    # The terminal is carried on the entry so a later readout (the
    # follow-mode drainer, or a focus read) tints the terminal the
    # summary CAME from, not whichever one is doing the speaking.
    entry = {"ts": int(time.time()), "project": project,
             "session": session_id, "text": spoken,
             "spoken": busy_pid is None and collision != "hold",
             "name": humanize(title) if title else speak_name(project),
             "tty": tty, "term": term,
             "color": ("#%02x%02x%02x" % rgb) if rgb else "off",
             "raise": resolve_raise(session_id)}
    if collision == "follow":
        entry["follow"] = True
    enqueue(entry)
    if collision == "hold":
        # Nothing speaks unprompted. The tab goes green and a soft chime
        # says "something arrived" — enough to notice when you're deep in
        # something, not enough to pull you out of it. It reads out when
        # you click into the terminal.
        spawn_watch()
        chime(after_pid=busy_pid)
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
    proc = subprocess.Popen(["/usr/bin/say", f"{entry['name']}: {spoken}"],
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


if __name__ == "__main__":
    try:
        if "--drain" in sys.argv:
            drain()
        elif "--untint" in sys.argv:
            untint(sys.argv[sys.argv.index("--untint") + 1])
        elif "--check-raise" in sys.argv:
            check_raise()
        elif "--repair" in sys.argv:
            repair()
        elif "--watch" in sys.argv:
            watch()
        else:
            main()
    finally:
        sys.exit(0)
