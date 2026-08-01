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
cp "$REPO_DIR/commands/tts.md" "$REPO_DIR/commands/spoken-recap.md" ~/.claude/commands/
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


def register(event, script, entry):
    global changed
    groups = hooks.setdefault(event, [])
    if any(h.get("command", "").endswith(script)
           for group in groups for h in group.get("hooks", [])):
        print(f"{event} hook already registered — leaving it as it is")
        return
    groups.append({"hooks": [entry]})
    changed = True
    print(f"{event} hook registered in ~/.claude/settings.json")


cmd = lambda name: "python3 " + os.path.expanduser(f"~/.claude/scripts/{name}")
# Speaks the summary. Async so it never delays the session.
register("Stop", "speak-response.py",
         {"type": "command", "command": cmd("speak-response.py"),
          "timeout": 15, "async": True})
# Catches `rr` and replays a summary without a model turn. Must be
# synchronous: it blocks the prompt (exit 2) so no tokens are spent.
register("UserPromptSubmit", "repeat-hook.py",
         {"type": "command", "command": cmd("repeat-hook.py"), "timeout": 25})

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
