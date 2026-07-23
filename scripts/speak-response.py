#!/usr/bin/env python3
"""Stop hook: speak Claude's response aloud via macOS `say` (on-device TTS).

Modes (per-session override > global default > "summary"):
  off     - stay silent
  summary - speak the final line marked with 🔊, else a sanitized capped fallback
  full    - speak the entire sanitized response (🔊 marker line dropped)

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
FALLBACK_CHAR_CAP = 600
MARKER = "🔊"
MODES = ("off", "summary", "full")
COLLISIONS = ("chime", "follow")
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


def pick_speech(text, mode):
    lines = text.splitlines()
    marker_line = next((ln.strip() for ln in reversed(lines)
                        if ln.strip().startswith(MARKER)), None)
    if mode == "full":
        body = "\n".join(ln for ln in lines if not ln.strip().startswith(MARKER))
        return sanitize(body)
    if marker_line:
        return sanitize(marker_line[len(MARKER):])
    spoken = sanitize(text)
    if len(spoken) > FALLBACK_CHAR_CAP:
        cut = spoken[:FALLBACK_CHAR_CAP]
        spoken = cut[:cut.rfind(" ")] + " — full response is in the terminal."
    return spoken


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
        proc.wait()


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
    text = fresh_assistant_text(transcript, session_id)
    if not text:
        return
    spoken = pick_speech(text, mode)
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
    entry = {"ts": int(time.time()), "project": project,
             "session": session_id, "text": spoken, "spoken": busy_pid is None,
             "name": humanize(title) if title else speak_name(project)}
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


if __name__ == "__main__":
    try:
        if "--drain" in sys.argv:
            drain()
        else:
            main()
    finally:
        sys.exit(0)
