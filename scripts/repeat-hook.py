#!/usr/bin/env python3
"""UserPromptSubmit hook: replay a spoken summary for free.

Missed what a terminal just said? Type `rr` and it speaks again — with
no model turn at all. The hook recognizes the trigger, plays the summary
itself, and exits 2, which tells Claude Code to erase the prompt and
never call the model. Nothing enters the conversation, so nothing is
added to context and no tokens are spent, the same way /context costs
nothing to look at.

Five names do the same thing — `rr`, `repeat`, `replay`, `recap`, `tts` —
so it works with whichever one you reach for. `<name>` below is any of them:

  <name>                 this session's most recent summary again
  <name> all             every session's unplayed summaries (all terminals)
  <name> full            this turn again IN FULL — the whole response, not
                         the one-line summary
  <name> inverse         this turn in whichever rendering you did NOT get:
                         full if you are in summary mode, summary if full
  <name> 3               pop the last 3 updates off the stack (any number)
  <name> 5m              pop everything from the last 5 minutes (s/m/h)
  <name> status          list this session's queue, speak nothing
  <name> stop / shh      shut the voice up right now
  stop                   same, but only while the voice is actually
                         talking — otherwise it is your prompt, not ours

`recap` and `tts` shadow the bare words only — the slash commands
`/spoken-recap`, `/recap` and `/tts` are untouched, since a slash prompt is
never the bare word. `tts off` and friends are not triggers either, so
configuring the voice still reaches Claude normally.

`full` and `inverse` cost nothing either: the hook payload carries this
session's transcript path, so the response is re-rendered from the
transcript with speak-response.py's own sanitizer. Nothing is sent to
the model, so a full re-read is as free as a summary.

`rr` is the least typing; the rest are there so you never have to recall
which one it was. Any other prompt falls through untouched (exit 0) and goes to Claude
normally — the triggers are exact matches, so a real prompt that merely
begins with "repeat" ("repeat the migration for the other table") is
never swallowed. Deliberately NOT a trigger: "again", which far more
often means "do that again" than "say that again".
"""
import json
import os
import re
import subprocess
import sys

# Overridable so the three scripts can be exercised against a scratch
# directory instead of the live queue and the real terminals. All three
# read the same variable, so one export isolates the whole tool.
CLAUDE_DIR = os.environ.get("CLAUDE_DIR") or os.path.expanduser("~/.claude")
RECAP = os.path.join(CLAUDE_DIR, "scripts", "tts-recap.py")
PID_FILE = os.path.join(CLAUDE_DIR, "scripts", ".speak-response.pid")


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
#
# Every name takes the WHOLE verb family, so the replay you get never
# depends on which name you happened to remember: `rr full`, `replay full`
# and `recap full` are one command. The names are deliberately redundant —
# a replay you cannot remember the word for is a replay you do not use, and
# the cost of an extra alias is one dict entry.
REPLAY_NAMES = ("rr", "repeat", "replay", "recap", "tts")
# suffix -> action. "" is the bare name: this session's latest.
REPLAY_VERBS = {"": "latest", " all": "all", " status": "status",
                " full": "full", " inverse": "inverse", " stop": "stop"}
TRIGGERS = {name + suffix: action
            for name in REPLAY_NAMES
            for suffix, action in REPLAY_VERBS.items()}
TRIGGERS.update({
            # One-word kill switch. Deliberately NOT bound to Esc: Esc
            # already interrupts the agent, and a key that sometimes
            # cancels your work and sometimes just silences audio is a
            # key you stop trusting. "shh" is three characters and can
            # never be mistaken for a real prompt.
            "shh": "stop", "hush": "stop",
            # Bare "stop" is ambiguous — it is also a perfectly normal
            # thing to say to Claude. So it only silences the voice WHEN
            # THE VOICE IS TALKING; otherwise it falls through and is
            # handled as the instruction it plainly was.
            "stop": "stop-if-speaking"})
SPEAKER = os.path.join(CLAUDE_DIR, "scripts", "speak-response.py")


def speaker_module():
    """Import speak-response.py so a replay renders text exactly the way
    the hook that spoke it would — same sanitizer, same cap, same naming.
    Re-deriving any of that here would drift the moment one side changed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("speak_response", SPEAKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stop_speaking():
    """Kill the readout in progress — mostly for a `repeat full` you've
    heard enough of."""
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return "repeat: nothing is speaking"
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True).stdout.strip()
        if not out.endswith("say"):
            return "repeat: nothing is speaking"
        os.kill(pid, 15)
    except (OSError, ValueError):
        return "repeat: nothing is speaking"
    return "repeat: stopped"


def replay_turn(payload, kind):
    """Re-read this session's last response, in full or in the mode you
    are not configured for. Free: the text comes from the transcript on
    disk, so the model is never involved."""
    transcript = payload.get("transcript_path")
    if not transcript or not os.path.exists(transcript):
        return "repeat: no transcript for this session"
    try:
        module = speaker_module()
        text, _ = module.last_assistant_text(transcript)
    except (OSError, ValueError, ImportError, AttributeError) as exc:
        return f"repeat: could not read the last response ({exc})"
    if not text:
        return "repeat: nothing said in this session yet"
    session_id = payload.get("session_id")
    mode = module.resolve_mode(session_id)
    if kind == "inverse":
        target = "summary" if mode == "full" else "full"
    else:
        target = "full"
    spoken = module.pick_speech(
        text, target, module.resolve_summary_cap(session_id, text))
    if not spoken:
        return "repeat: nothing to say"
    title = module.session_name(session_id)
    name = (module.humanize(title) if title
            else module.speak_name(os.path.basename(payload.get("cwd") or "")))
    proc = subprocess.Popen(["/usr/bin/say", f"{name}: {spoken}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(proc.pid))
    except OSError:
        pass
    tty = module.owning_tty()
    if module.tint_start(tty, os.environ.get("TERM_PROGRAM") or "",
                         module.resolve_tab_color(session_id), proc.pid):
        module.spawn_untinter(tty)
    words = len(spoken.split())
    return (f"repeat: reading the {target} version (~{words} words)"
            + (" — `repeat stop` to cut it off" if words > 120 else ""))


# <name> <number> pops that many updates off the stack; <name> <number><unit>
# pops a window of time. Anything else after a name is NOT a trigger and
# falls through to Claude, so "repeat 3 times for the other table" is safe.
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
         "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
         "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600}


def parse_trigger(prompt):
    """(action, argument) for a replay prompt, else (None, None).

    Exact word triggers first, then the stack forms: `rr 3` (count) and
    `rr 5m` (window). A count is the primary shape — the queue is a stack
    of updates and you pop the top N — with the time form there for when
    you think in "since I walked away" instead of in updates.
    """
    p = " ".join((prompt or "").strip().lower().split())
    if p in TRIGGERS:
        return TRIGGERS[p], None
    parts = p.split()
    if len(parts) != 2 or parts[0] not in REPLAY_NAMES:
        return None, None
    m = re.fullmatch(r"(\d+)([a-z]*)", parts[1])
    if not m:
        return None, None
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        return None, None
    if not unit:
        return "last", n
    if unit in UNITS:
        return "since", n * UNITS[unit]
    return None, None


def recap_args(kind, session_id, arg=None):
    mine = ["--session", session_id] if session_id else []
    if kind == "latest":
        return ["--latest"] + mine
    if kind == "last":
        return ["--last", str(arg)] + mine
    if kind == "since":
        return ["--since", str(arg)] + mine
    if kind == "all":
        return []
    return ["--status"] + mine


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    kind, arg = parse_trigger(payload.get("prompt"))
    if kind is None:
        return 0  # not for us — hand the prompt to Claude untouched
    if kind == "stop-if-speaking":
        if not busy():
            return 0  # nothing talking — you meant it for Claude
        kind = "stop"
    if kind == "stop":
        print(stop_speaking(), file=sys.stderr)
        return 2
    if not os.path.exists(RECAP):
        print("repeat: tts-recap.py is not installed", file=sys.stderr)
        return 2
    if kind != "status" and busy():
        # Speaking now would collide; the queue is still there to replay.
        print("repeat: something is speaking right now — `repeat stop` to cut "
              "it off, or try again in a moment", file=sys.stderr)
        return 2
    if kind in ("full", "inverse"):
        print(replay_turn(payload, kind), file=sys.stderr)
        return 2
    try:
        out = subprocess.run(
            [sys.executable, RECAP] + recap_args(kind, payload.get("session_id"), arg),
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
