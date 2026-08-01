#!/usr/bin/env python3
"""Speak queued TTS summaries that were chimed past while something else
was talking. Used by the /spoken-recap skill.

The queue is a STACK of updates: every finished turn pushes one down, and a
replay pops however many you ask for off the top. That is the mental model —
you do not ask "what is still unplayed", you ask for the last N.

  tts-recap.py            speak all unplayed entries oldest-first, mark played
  tts-recap.py --latest   speak the most recent entry (even if already played)
  tts-recap.py --last N   pop the N most recent entries, played or not
  tts-recap.py --since S  pop everything from the last S seconds
  tts-recap.py --status   print unplayed count and entries, speak nothing
  --session <id>          restrict any of the above to one session's entries

`--last` and `--since` deliberately IGNORE the played flag: "the last 3"
means the last 3, whether or not you caught them live. Both narrate
oldest-first inside the window, so a pop of 3 plays in the order it
happened rather than backwards, and both mark what they read as played so
a later bare recap does not repeat it.

Queue: ~/.claude/tts-queue.jsonl, written by speak-response.py. Trimmed to
the newest 200 entries on every run.
"""
import json
import os
import re
import subprocess
import sys
import time


def int_arg(flag):
    """Positive integer following `flag`, or None. Anything else is None
    rather than an error: a malformed replay should fall back to normal
    behaviour, never blow up a hook."""
    if flag not in sys.argv:
        return None
    try:
        val = int(sys.argv[sys.argv.index(flag) + 1])
    except (IndexError, ValueError):
        return None
    return val if val > 0 else None


def session_filter():
    if "--session" in sys.argv:
        try:
            return sys.argv[sys.argv.index("--session") + 1]
        except IndexError:
            pass
    return None

# Overridable so the three scripts can be exercised against a scratch
# directory instead of the live queue. All three read the same variable.
CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude")
QUEUE_FILE = os.path.join(CLAUDE_DIR, "tts-queue.jsonl")
PID_FILE = os.path.join(CLAUDE_DIR, "scripts", ".speak-response.pid")
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
        with open(os.path.join(CLAUDE_DIR, "tts-state.json")) as f:
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
    last_n, since_s = int_arg("--last"), int_arg("--since")
    if last_n or since_s:
        if last_n:
            window, what = mine[-last_n:], f"last {last_n}"
        else:
            cutoff = time.time() - since_s
            window = [e for e in mine if e.get("ts", 0) >= cutoff]
            what = (f"last {since_s // 60} min" if since_s >= 60
                    else f"last {since_s}s")
        if not window:
            print(f"nothing in the {what}")
            return
        speak(" ... Next. ".join(
            f"{e.get('name') or speak_name(e.get('project'))}: {e.get('text')}"
            for e in window))
        for e in window:
            e["spoken"] = True
        save_queue(entries)
        n = len(window)
        print(f"popping {n} update{'' if n == 1 else 's'} ({what}):")
        for e in window:
            print(f"- [{e.get('project')}] {e.get('text')}")
        return
    if "--latest" in sys.argv:
        if not mine:
            print("queue empty")
            return
        e = mine[-1]
        speak(f"{e.get('name') or speak_name(e.get('project'))}: {e.get('text')}")
        e["spoken"] = True
        save_queue(entries)
        print(f"replaying latest: [{e.get('project')}] {e.get('text')}")
        return
    unplayed = [e for e in mine if not e.get("spoken")]
    if not unplayed:
        print("nothing queued — you're caught up")
        return
    chunks = [f"{e.get('name') or speak_name(e.get('project'))}: {e.get('text')}"
              for e in unplayed]
    speak(" ... Next. ".join(chunks))
    for e in unplayed:
        e["spoken"] = True
    save_queue(entries)
    print(f"speaking {len(unplayed)} queued summar{'y' if len(unplayed) == 1 else 'ies'}:")
    for e in unplayed:
        print(f"- [{e.get('project')}] {e.get('text')}")


if __name__ == "__main__":
    main()
