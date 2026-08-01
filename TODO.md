# TODO

Ideas and open questions for claude-speaker. Nothing here is committed work.

## How can this work on my iPhone?

The pitch that makes this worth doing: you walk away from the desk, and the
terminal that finished still reaches you.

Today everything is deliberately local — `say` and `afplay` on the machine
running Claude Code, no network, no accounts. Getting to a phone breaks that
assumption, so the question is which trade to make:

- **Notification only** (no audio). Push a banner to the phone when a summary
  lands. Cheapest path is something like ntfy.sh / Pushover from the Stop hook —
  one HTTP POST, no app to write. Downside: the summary text leaves the machine,
  which is the thing this tool has so far never done. Would want it opt-in and
  clearly labeled.
- **Audio on the phone.** Much bigger: needs the text synthesized somewhere the
  phone can play it, or a companion app doing TTS on-device. Probably not worth
  it — a banner you can read is usually enough to decide whether to walk back.
- **Handoff via Focus/Continuity.** Worth checking whether a Mac notification
  already mirrors to iPhone under the right Focus settings. If it does, the
  phone story might be mostly a documentation task rather than a feature.
- **Open question:** does the queue follow you? If three summaries pile up while
  you're away, does the phone get three banners, one digest, or nothing until you
  come back? The desk version answers this with color aging; the phone version
  needs its own answer.

Start by checking the Continuity path — it costs nothing and might make the rest
unnecessary.

## Smaller things noticed while building

- `/spoken-recap` doesn't tint the terminal it's reading for, because one
  utterance can span several sessions. Fine for now; would need per-entry
  playback to fix.
- Tinting is Terminal.app and iTerm2 only. Ghostty, WezTerm and Kitty speak fine
  but show no color. Worth checking whether any of them support a tab-color
  escape.
- The waiting ladder (30s / 5min) is fixed. Nobody has asked to change it yet —
  wait until someone does.
- No aging chime: a summary chimes once on arrival and then only changes color.
  Deliberate (nagging is worse than silence), but revisit if summaries get
  missed.
