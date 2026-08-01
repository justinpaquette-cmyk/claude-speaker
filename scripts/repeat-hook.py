#!/usr/bin/env python3
"""UserPromptSubmit hook: replay a spoken summary for free.

Missed what a terminal just said? Type `rr` and it speaks again — with
no model turn at all. The hook recognizes the trigger, plays the summary
itself, and exits 2, which tells Claude Code to erase the prompt and
never call the model. Nothing enters the conversation, so nothing is
added to context and no tokens are spent, the same way /context costs
nothing to look at.

  repeat  / rr           this session's most recent summary again
  repeat all / rr all    every session's unplayed summaries (all terminals)
  repeat status          list this session's queue, speak nothing

`repeat` is the memorable name; `rr` is the same thing with less typing.
Any other prompt falls through untouched (exit 0) and goes to Claude
normally — the triggers are exact matches, so a real prompt that merely
begins with "repeat" ("repeat the migration for the other table") is
never swallowed. Deliberately NOT a trigger: "again", which far more
often means "do that again" than "say that again".
"""
import json
import os
import subprocess
import sys

RECAP = os.path.expanduser("~/.claude/scripts/tts-recap.py")
PID_FILE = os.path.expanduser("~/.claude/scripts/.speak-response.pid")


def busy():
    """True while another summary is being read — don't talk over it."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True).stdout.strip()
        return out.endswith("say")
    except (OSError, ValueError):
        return False


# Exact prompts that mean "say that again" rather than "do that again".
TRIGGERS = {"rr": "latest", "repeat": "latest",
            "rr all": "all", "repeat all": "all",
            "rr status": "status", "repeat status": "status"}


def recap_args(kind, session_id):
    mine = ["--session", session_id] if session_id else []
    if kind == "latest":
        return ["--latest"] + mine
    if kind == "all":
        return []
    return ["--status"] + mine


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    kind = TRIGGERS.get((payload.get("prompt") or "").strip().lower())
    if kind is None:
        return 0  # not for us — hand the prompt to Claude untouched
    if not os.path.exists(RECAP):
        print("repeat: tts-recap.py is not installed", file=sys.stderr)
        return 2
    if kind != "status" and busy():
        # Speaking now would collide; the queue is still there to replay.
        print("repeat: something is speaking right now — try again in a moment",
              file=sys.stderr)
        return 2
    try:
        out = subprocess.run(
            [sys.executable, RECAP] + recap_args(kind, payload.get("session_id")),
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"repeat: replay failed ({exc})", file=sys.stderr)
        return 2
    # Exit 2 shows stderr to you and nothing to the model, so the recap
    # output has to go out on stderr to be visible at all.
    print((out.stdout or out.stderr or "repeat: nothing to replay").strip(),
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
