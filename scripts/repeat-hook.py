#!/usr/bin/env python3
"""UserPromptSubmit hook: replay a spoken summary for free.

Missed what a terminal just said? Type `rr` and it speaks again — with
no model turn at all. The hook recognizes the trigger, plays the summary
itself, and exits 2, which tells Claude Code to erase the prompt and
never call the model. Nothing enters the conversation, so nothing is
added to context and no tokens are spent, the same way /context costs
nothing to look at.

  rr           speak this session's most recent summary again
  rr all       speak every session's unplayed summaries (all terminals)
  rr status    list this session's queue, speak nothing

Any other prompt falls through untouched (exit 0) and goes to Claude
normally — the triggers are exact matches, so a real prompt that merely
starts with "rr" is never swallowed.
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


def recap_args(trigger, session_id):
    if trigger == "rr":
        return ["--latest"] + (["--session", session_id] if session_id else [])
    if trigger == "rr all":
        return []
    return ["--status"] + (["--session", session_id] if session_id else [])


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    trigger = (payload.get("prompt") or "").strip().lower()
    if trigger not in ("rr", "rr all", "rr status"):
        return 0  # not for us — hand the prompt to Claude untouched
    if not os.path.exists(RECAP):
        print("rr: tts-recap.py is not installed", file=sys.stderr)
        return 2
    if trigger != "rr status" and busy():
        # Speaking now would collide; the queue is still there to replay.
        print("rr: something is speaking right now — try again in a moment",
              file=sys.stderr)
        return 2
    try:
        out = subprocess.run(
            [sys.executable, RECAP] + recap_args(trigger, payload.get("session_id")),
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"rr: replay failed ({exc})", file=sys.stderr)
        return 2
    # Exit 2 shows stderr to you and nothing to the model, so the recap
    # output has to go out on stderr to be visible at all.
    print((out.stdout or out.stderr or "rr: nothing to replay").strip(),
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
