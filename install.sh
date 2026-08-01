#!/bin/bash
# claude-speaker installer — free, on-device TTS for Claude Code on macOS.
# Idempotent: safe to re-run after updates.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "claude-speaker uses macOS's built-in \`say\` — macOS only." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p ~/.claude/scripts ~/.claude/commands
cp "$REPO_DIR/scripts/speak-response.py" "$REPO_DIR/scripts/tts-recap.py" \
   "$REPO_DIR/scripts/repeat-hook.py" ~/.claude/scripts/
chmod +x ~/.claude/scripts/speak-response.py ~/.claude/scripts/tts-recap.py \
         ~/.claude/scripts/repeat-hook.py
cp "$REPO_DIR/commands/tts.md" "$REPO_DIR/commands/spoken-recap.md" \
   "$REPO_DIR/commands/tts-wizard.md" ~/.claude/commands/
echo "Installed scripts to ~/.claude/scripts and commands to ~/.claude/commands"

# Build the mic/camera detector (call suppression). Optional: without it,
# speech simply always plays.
if command -v cc >/dev/null 2>&1; then
  if cc -O2 -o ~/.claude/scripts/av-status "$REPO_DIR/scripts/av-status.c" \
       -framework CoreAudio -framework CoreMediaIO -framework CoreFoundation; then
    echo "Built ~/.claude/scripts/av-status (speech pauses while mic/camera are in use)"
  else
    echo "WARNING: av-status failed to build — speech will play even during calls" >&2
  fi
else
  echo "WARNING: no C compiler (install Xcode Command Line Tools) — speech will play even during calls" >&2
fi

# Build the menu-bar indicator. Optional: without it, colors and speech
# still work, there is just nothing in the top bar.
if command -v swiftc >/dev/null 2>&1; then
  if swiftc -O -o ~/.claude/scripts/speaking-badge "$REPO_DIR/scripts/speaking-badge.swift"; then
    echo "Built ~/.claude/scripts/speaking-badge (menu-bar indicator)"
  else
    echo "WARNING: speaking-badge failed to build — no menu-bar indicator" >&2
  fi
else
  echo "WARNING: no swiftc — no menu-bar indicator (everything else works)" >&2
fi

# Register the hooks in ~/.claude/settings.json (merge, never clobber).
python3 - <<'PY'
import json, os

path = os.path.expanduser("~/.claude/settings.json")
try:
    with open(path) as f:
        settings = json.load(f)
except (OSError, ValueError):
    settings = {}

hooks = settings.setdefault("hooks", {})
changed = False


def register(event, script, entry, matcher=None, label=None):
    """Add one hook, unless an equivalent one is already there.

    Matched on the FULL command, not the script name: several hooks share
    speak-response.py and differ only by flag (--ask-open, --ask-close), so
    a name match would register the first and silently skip the rest.
    """
    global changed
    groups = hooks.setdefault(event, [])
    name = label or script
    if any(h.get("command", "") == entry["command"]
           for group in groups for h in group.get("hooks", [])):
        print(f"{name} hook already registered — leaving it as it is")
        return
    group = {"hooks": [entry]}
    if matcher:
        group["matcher"] = matcher
    groups.append(group)
    changed = True
    print(f"{name} hook registered in ~/.claude/settings.json")


cmd = lambda name: "python3 " + os.path.expanduser(f"~/.claude/scripts/{name}")
# Speaks the summary. Async so it never delays the session.
register("Stop", "speak-response.py",
         {"type": "command", "command": cmd("speak-response.py"),
          "timeout": 15, "async": True})
# Catches `rr` and replays a summary without a model turn. Must be
# synchronous: it blocks the prompt (exit 2) so no tokens are spent.
register("UserPromptSubmit", "repeat-hook.py",
         {"type": "command", "command": cmd("repeat-hook.py"), "timeout": 25})
# Colors (and, outside `hold`, announces) a terminal that is blocked on an
# AskUserQuestion. PreToolUse blocks the question from rendering until it
# returns, so it only writes a marker and hands the slow part to a child.
register("PreToolUse", "speak-response.py",
         {"type": "command", "command": cmd("speak-response.py") + " --ask-open",
          "timeout": 10},
         matcher="AskUserQuestion", label="ask-open")
register("PostToolUse", "speak-response.py",
         {"type": "command", "command": cmd("speak-response.py") + " --ask-close",
          "timeout": 10},
         matcher="AskUserQuestion", label="ask-close")

if changed:
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
PY

# Teach Claude the spoken-summary convention (skip if already present).
CLAUDE_MD=~/.claude/CLAUDE.md
touch "$CLAUDE_MD"
if grep -qF "## Spoken Summary (TTS)" "$CLAUDE_MD"; then
  echo "Spoken-summary convention already in ~/.claude/CLAUDE.md"
else
  cat "$REPO_DIR/claude-md-snippet.md" >> "$CLAUDE_MD"
  echo "Spoken-summary convention appended to ~/.claude/CLAUDE.md"
fi

echo
echo "Done. Restart your Claude Code sessions (or open /hooks once in each) to activate."
say "Claude can talk now."
