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
cp "$REPO_DIR/scripts/speak-response.py" "$REPO_DIR/scripts/tts-recap.py" ~/.claude/scripts/
chmod +x ~/.claude/scripts/speak-response.py ~/.claude/scripts/tts-recap.py
cp "$REPO_DIR/commands/tts.md" "$REPO_DIR/commands/spoken-recap.md" ~/.claude/commands/
echo "Installed scripts to ~/.claude/scripts and commands to ~/.claude/commands"

# Register the Stop hook in ~/.claude/settings.json (merge, never clobber).
python3 - <<'PY'
import json, os

path = os.path.expanduser("~/.claude/settings.json")
try:
    with open(path) as f:
        settings = json.load(f)
except (OSError, ValueError):
    settings = {}

command = "python3 " + os.path.expanduser("~/.claude/scripts/speak-response.py")
stop = settings.setdefault("hooks", {}).setdefault("Stop", [])
already = any(h.get("command", "").endswith("speak-response.py")
              for group in stop for h in group.get("hooks", []))
if already:
    print("Stop hook already registered — leaving settings.json untouched")
else:
    stop.append({"hooks": [{"type": "command", "command": command,
                            "timeout": 15, "async": True}]})
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print("Stop hook registered in ~/.claude/settings.json")
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
