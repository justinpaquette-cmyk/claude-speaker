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
is currently speaking, it plays immediately (spoken: true); if another
session is talking, a chime plays instead and the entry waits (spoken:
false) for /spoken-recap (scripts/tts-recap.py). Always exits 0 — TTS must
never block the session.
"""
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
FALLBACK_CHAR_CAP = 600
MARKER = "🔊"
MODES = ("off", "summary", "full")


def read_mode(path):
    try:
        with open(path) as f:
            mode = (json.load(f).get("mode") or "").strip()
        return mode if mode in MODES else None
    except (OSError, ValueError):
        return None


def resolve_mode(session_id):
    if session_id:
        mode = read_mode(os.path.join(SESSION_DIR, f"{session_id}.json"))
        if mode:
            return mode
    return read_mode(STATE_FILE) or "summary"


def last_assistant_text(transcript_path):
    text = None
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
    try:
        text = last_assistant_text(transcript)
    except OSError:
        return
    if not text:
        return
    spoken = pick_speech(text, mode)
    if not spoken:
        return
    project = os.path.basename(payload.get("cwd") or "") or "unknown"
    busy_pid = active_say_pid()
    enqueue({"ts": int(time.time()), "project": project,
             "session": session_id, "text": spoken, "spoken": busy_pid is None})
    if busy_pid is not None:
        # Something is already talking: don't collide. Chime AFTER the current
        # speech finishes so neither is masked; the summary waits in the
        # queue for /spoken-recap.
        subprocess.Popen(
            ["/bin/sh", "-c",
             f"while kill -0 {busy_pid} 2>/dev/null; do sleep 0.3; done; "
             "/usr/bin/afplay -v 0.6 /System/Library/Sounds/Glass.aiff"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        return
    proc = subprocess.Popen(["/usr/bin/say", spoken],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.exit(0)
