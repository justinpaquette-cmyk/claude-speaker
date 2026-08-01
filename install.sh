#!/bin/bash
# claude-speaker installer — free, on-device TTS for Claude Code on macOS.
# Idempotent: safe to re-run after updates.
#
#   ./install.sh           copy the managed files into ~/.claude (default)
#   ./install.sh --link    symlink them instead — for working ON this repo
#   ./install.sh --check   report whether what's running is what's committed
#
# The hooks never run this repo; they run whatever is at
# ~/.claude/scripts/*.py. With `cp` that is a SECOND COPY, and an edit to
# the one that runs is invisible to git — which is how ~290 lines of
# unversioned work once accumulated in the installed speak-response.py,
# one `install.sh` run from deletion. `--link` removes the second copy
# altogether: there is one file, edits are live on the next hook
# invocation, and anything uncommitted shows up in `git status`.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "claude-speaker uses macOS's built-in \`say\` — macOS only." >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

# Every file the installer owns, repo-relative. ~/.claude/scripts also
# holds unrelated tooling, so these are handled one file at a time and
# the directory itself is never linked or replaced.
MANAGED=(
  scripts/speak-response.py
  scripts/tts-recap.py
  scripts/repeat-hook.py
  commands/tts.md
  commands/spoken-recap.md
  commands/tts-wizard.md
)
PY_MANAGED=(
  scripts/speak-response.py
  scripts/tts-recap.py
  scripts/repeat-hook.py
)

LINK=0
CHECK=0
for arg in "$@"; do
  case "$arg" in
    --link)  LINK=1 ;;
    --check) CHECK=1 ;;
    -h|--help)
      sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "install.sh: unknown option '$arg' (try --help)" >&2
      exit 2 ;;
  esac
done

# Syntax-check one file without leaving a .pyc next to it — under --link
# that directory is the repo, and a stray cache file is noise in
# `git status`. Compiles to a temp path instead of __pycache__.
py_check() {
  python3 - "$1" <<'PY'
import os, py_compile, sys, tempfile
fd, tmp = tempfile.mkstemp(suffix=".pyc")
os.close(fd)
try:
    py_compile.compile(sys.argv[1], cfile=tmp, doraise=True)
except py_compile.PyCompileError as e:
    print(e.msg.strip(), file=sys.stderr)
    sys.exit(1)
finally:
    os.unlink(tmp)
PY
}

# --check: one command that answers "is what's running what's committed?"
# Under --link that is a tautology and every file reports `linked`; the
# compile pass is the part that still earns its keep there, since a syntax
# error in the repo is live the instant it is saved.
if [[ "$CHECK" == 1 ]]; then
  bad=0
  for rel in "${MANAGED[@]}"; do
    src="$REPO_DIR/$rel"
    dest="$HOME/.claude/$rel"
    if [[ -L "$dest" ]]; then
      target="$(readlink "$dest")"
      if [[ ! -e "$dest" ]]; then
        # Dangling: the repo moved, was renamed, or the file was deleted.
        # The hook fails silently in this state, so it must not read as OK.
        printf '  %-28s BROKEN LINK -> %s (gone)\n' "$rel" "$target"
        bad=1
      elif [[ "$target" == "$src" ]]; then
        printf '  %-28s linked\n' "$rel"
      else
        printf '  %-28s LINKED ELSEWHERE -> %s\n' "$rel" "$target"
        bad=1
      fi
    elif [[ ! -e "$dest" ]]; then
      printf '  %-28s MISSING (not installed)\n' "$rel"
      bad=1
    elif cmp -s "$src" "$dest"; then
      printf '  %-28s identical\n' "$rel"
    else
      printf '  %-28s DRIFTED (installed copy differs from the repo)\n' "$rel"
      bad=1
    fi
  done
  echo
  for rel in "${PY_MANAGED[@]}"; do
    # Compile what actually RUNS, not the repo copy — under --link they
    # are the same file, and under `cp` a drifted install is exactly when
    # you want to know its copy is broken.
    target="$HOME/.claude/$rel"
    [[ -e "$target" ]] || target="$REPO_DIR/$rel"
    if py_check "$target"; then
      printf '  %-28s compiles\n' "$rel"
    else
      printf '  %-28s DOES NOT COMPILE\n' "$rel"
      bad=1
    fi
  done
  echo
  if [[ "$bad" == 1 ]]; then
    echo "Not clean." >&2
    echo "  DRIFTED  an edit is live that git has never seen. Reconcile it" >&2
    echo "           (git merge-file) BEFORE re-running install.sh, which" >&2
    echo "           overwrites the installed copy." >&2
    echo "  BROKEN / LINKED ELSEWHERE / MISSING  the hook is not running this" >&2
    echo "           repo at all. Re-run install.sh (or --link) to repair." >&2
    exit 1
  fi
  echo "Clean — the installed files match the repo and all scripts compile."
  exit 0
fi

# Never `cp`/`ln` onto an existing destination: if it is already a symlink
# into the repo, `cp` writes THROUGH it onto the source file (or aborts as
# "identical file" under set -e). Removing first makes both paths safe
# regardless of what the previous install left behind.
install_one() {
  local rel="$1"
  local src="$REPO_DIR/$rel"
  local dest="$HOME/.claude/$rel"

  if [[ "$LINK" == 1 ]]; then
    rm -f "$dest"
    ln -s "$src" "$dest"
    return
  fi

  # Copy path only: keep whatever is about to be overwritten. `-f` follows
  # symlinks, so `! -L` is what restricts this to a real second copy.
  if [[ -f "$dest" && ! -L "$dest" ]] && ! cmp -s "$src" "$dest"; then
    local bak="$dest.bak-$(date +%Y%m%d-%H%M%S)"
    cp -p "$dest" "$bak"
    echo "  kept the installed $rel (it differed) at $bak"
  fi
  rm -f "$dest"
  cp "$src" "$dest"
  chmod +x "$dest" 2>/dev/null || true
}

mkdir -p ~/.claude/scripts ~/.claude/commands
for rel in "${MANAGED[@]}"; do
  install_one "$rel"
done
if [[ "$LINK" == 1 ]]; then
  # No chmod here on purpose: it would follow the link and change the
  # repo's own file modes as a side effect. The scripts are committed 755.
  echo "Linked scripts into ~/.claude/scripts and commands into ~/.claude/commands"
else
  echo "Installed scripts to ~/.claude/scripts and commands to ~/.claude/commands"
fi

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
if [[ "$LINK" == 1 ]]; then
  echo "Linked install: the hooks now run this working tree directly, from"
  echo "  $REPO_DIR"
  echo "Move or rename it and the hooks break silently — \`./install.sh --check\`"
  echo "reports that. Edits are live on the next hook invocation, but a running"
  echo "watcher holds its old code until the queue clears."
  echo
fi
echo "Done. Restart your Claude Code sessions (or open /hooks once in each) to activate."
say "Claude can talk now."
