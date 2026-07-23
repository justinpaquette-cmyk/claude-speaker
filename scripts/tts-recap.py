#!/usr/bin/env python3
"""Speak queued TTS summaries that were chimed past while something else
was talking. Used by the /spoken-recap skill.

  tts-recap.py            speak all unplayed entries oldest-first, mark played
  tts-recap.py --latest   speak the most recent entry (even if already played)
  tts-recap.py --status   print unplayed count and entries, speak nothing
  --session <id>          restrict any of the above to one session's entries

Queue: ~/.claude/tts-queue.jsonl, written by speak-response.py. Trimmed to
the newest 200 entries on every run.
"""
import json
import os
import re
import subprocess
import sys


def session_filter():
    if "--session" in sys.argv:
        try:
            return sys.argv[sys.argv.index("--session") + 1]
        except IndexError:
            pass
    return None

QUEUE_FILE = os.path.expanduser("~/.claude/tts-queue.jsonl")
PID_FILE = os.path.expanduser("~/.claude/scripts/.speak-response.pid")
KEEP = 200


def load_queue():
    entries = []
    try:
        with open(QUEUE_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def save_queue(entries):
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for e in entries[-KEEP:]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    os.replace(tmp, QUEUE_FILE)


def speak_name(project):
    """Spoken form of a project name: custom name from tts-state.json's
    "names" map if set, else camelCase/dashes split into words."""
    try:
        with open(os.path.expanduser("~/.claude/tts-state.json")) as f:
            names = json.load(f).get("names") or {}
        if project in names:
            return names[project]
    except (OSError, ValueError):
        pass
    name = re.sub(r"[-_]+", " ", project or "")
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)


def speak(text):
    proc = subprocess.Popen(["/usr/bin/say", text],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass


def main():
    entries = load_queue()
    sess = session_filter()
    mine = [e for e in entries if sess is None or e.get("session") == sess]
    if "--status" in sys.argv:
        unplayed = [e for e in mine if not e.get("spoken")]
        print(f"unplayed: {len(unplayed)}")
        for e in unplayed:
            print(f"- [{e.get('project')}] {e.get('text')}")
        return
    if "--latest" in sys.argv:
        if not mine:
            print("queue empty")
            return
        e = mine[-1]
        speak(f"{speak_name(e.get('project'))}: {e.get('text')}")
        e["spoken"] = True
        save_queue(entries)
        print(f"replaying latest: [{e.get('project')}] {e.get('text')}")
        return
    unplayed = [e for e in mine if not e.get("spoken")]
    if not unplayed:
        print("nothing queued — you're caught up")
        return
    chunks = [f"{speak_name(e.get('project'))}: {e.get('text')}" for e in unplayed]
    speak(" ... Next. ".join(chunks))
    for e in unplayed:
        e["spoken"] = True
    save_queue(entries)
    print(f"speaking {len(unplayed)} queued summar{'y' if len(unplayed) == 1 else 'ies'}:")
    for e in unplayed:
        print(f"- [{e.get('project')}] {e.get('text')}")


if __name__ == "__main__":
    main()
