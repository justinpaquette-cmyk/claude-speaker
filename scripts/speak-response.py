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

Delivery: every summary is appended to ~/.claude/tts-queue.jsonl. If nothing
is currently speaking, it plays immediately (spoken: true). If another
session is talking, the "collision" setting (per-session override > global
> "chime") decides:
  chime  - a chime plays after the current speech and the entry waits
           (spoken: false) for /spoken-recap (scripts/tts-recap.py)
  follow - the entry waits its turn and is spoken automatically right
           after the current speech ends (a locked drainer subprocess,
           `speak-response.py --drain`, serializes the readout)
If the mic or camera is
live (a call, a recording — checked via the compiled av-status helper),
nothing plays at all, not even the chime; the entry just queues. Always
exits 0 — TTS must never block the session.

Visual cue: while a summary is being read, the terminal it came from is
tinted red (Terminal.app tab background via AppleScript, iTerm2 tab color
via OSC 6) and restored the moment the voice stops — so you can see which
session is talking, not just hear it. Off (or recolored) via `/tts color`.
The original color is parked in ~/.claude/tts-tabcolor/<tty>.json so a
killed watcher can't strand a terminal red: the next hook run restores it.

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
DEFAULT_TAB_COLOR = "red"
# Adaptive summary cap: proportional to the turn's sanitized volume, clamped.
# An explicit /tts length (summary_chars) overrides this with a flat cap.
SUMMARY_RATIO = 0.5
SUMMARY_FLOOR = 400
SUMMARY_CEILING = 2500
MARKER = "🔊"
MODES = ("off", "summary", "full")
COLLISIONS = ("chime", "follow")
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
    return resolve_setting(session_id, "raise", RAISES, "window")


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
        _osascript(_terminal_tabs(
            f'if (tty of t) is "{tty}" then '
            f"set background color of t to {{{r}, {g}, {b}}}"))
        return True
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


def raise_window(tty, term):
    """Bring the speaking terminal's window to the top of the stack.

    Uses System Events' AXRaise, which reorders the window in place and
    leaves keyboard focus exactly where it was. Deliberately NOT
    `activate` — that would yank focus out of whatever you are typing in.
    Needs Accessibility permission for the terminal app; without it this
    silently does nothing (`speak-response.py --check-raise` says so).
    """
    proc = AX_PROCESS.get(term)
    if not proc or not tty:
        return False
    return _osascript(f'''{RAISE_SCRIPT[term] % {"tty": tty}}
if idx is 0 then return "no-window"
tell application "System Events" to tell process "{proc}"
if idx > (count of windows) then return "no-ax-window"
perform action "AXRaise" of window idx
end tell
return "ok"''') == "ok"


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


def tint_start(tty, term, rgb, say_pid):
    """Tint `tty` for the duration of a readout, parking what to restore.

    The restore record carries the speaking `say` pid so a killed watcher
    can't strand the terminal: restore_stale() finishes the job later.
    """
    if not tty or not rgb or not term:
        return False
    saved = _read_restore(tty)
    # Already tinted (stale flash): keep the ORIGINAL color, not the tint.
    original = saved.get("restore") if saved else (
        _terminal_bg(tty) if term == "Apple_Terminal" else None)
    if term == "Apple_Terminal" and original is None:
        return False
    if not _paint(tty, term, rgb):
        return False
    try:
        os.makedirs(TABCOLOR_DIR, exist_ok=True)
        with open(_tabcolor_path(tty), "w") as f:
            json.dump({"term": term, "restore": original, "pid": say_pid}, f)
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
    """Put `tty` back to the color it had before the readout."""
    rec = _read_restore(tty)
    if not rec:
        return
    restore = rec.get("restore")
    _paint(tty, rec.get("term"), tuple(restore) if restore else None)
    try:
        os.remove(_tabcolor_path(tty))
    except OSError:
        pass


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
    """Un-tint terminals whose readout is over but whose watcher died."""
    try:
        files = os.listdir(TABCOLOR_DIR)
    except OSError:
        return
    for fn in files:
        if not fn.endswith(".json"):
            continue
        tty = "/dev/" + fn[:-len(".json")]
        if still_speaking((_read_restore(tty) or {}).get("pid")):
            continue
        tint_stop(tty)


def spawn_watcher(tty):
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
    if on_call():
        # Mic or camera is live — Justin is probably on a call. Total
        # silence (even the chime would bleed into a meeting); the summary
        # waits in the queue for /spoken-recap.
        enqueue({"ts": int(time.time()), "project": project,
                 "session": session_id, "text": spoken, "spoken": False,
                 "held": "call"})
        return
    busy_pid = active_say_pid()
    collision = resolve_collision(session_id)
    # Resolve the announce-name now, while the session registry entry is
    # alive: /rename (or auto) session title > /tts names map > folder name.
    title = session_name(session_id)
    # This session's terminal, carried on the entry so a later readout
    # (follow-mode drainer) tints the terminal the summary CAME from.
    rgb = resolve_tab_color(session_id)
    tty = owning_tty()
    term = os.environ.get("TERM_PROGRAM") or ""
    entry = {"ts": int(time.time()), "project": project,
             "session": session_id, "text": spoken, "spoken": busy_pid is None,
             "name": humanize(title) if title else speak_name(project),
             "tty": tty, "term": term,
             "color": ("#%02x%02x%02x" % rgb) if rgb else "off",
             "raise": resolve_raise(session_id)}
    if collision == "follow":
        entry["follow"] = True
    enqueue(entry)
    if busy_pid is not None:
        # Something is already talking: don't collide.
        if collision == "follow":
            # The summary speaks automatically right after the current
            # speech (and any earlier queued entries) — no chime.
            spawn_drainer()
            return
        # chime (default): chime AFTER the current speech finishes so
        # neither is masked; the summary waits for /spoken-recap.
        subprocess.Popen(
            ["/bin/sh", "-c",
             f"while kill -0 {busy_pid} 2>/dev/null; do sleep 0.3; done; "
             "/usr/bin/afplay -v 0.6 /System/Library/Sounds/Glass.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return
    # Name-prefixed on every path — immediate, drainer, /spoken-recap — so a
    # summary is always attributable to a terminal, contention or not.
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
        spawn_watcher(tty)
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
        else:
            main()
    finally:
        sys.exit(0)
