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
- **The queue outgrows `KEEP` between recaps.** `KEEP = 200`, but the live queue
  measured **210** entries on 2026-08-01 and **227** a few hours later the same
  day — it is growing faster than anything cuts it back. The cause is not only
  that `enqueue()` appends without trimming: **no writer in speak-response.py
  caps the file at all**, since its own `save_queue()` rewrites every entry it
  was given (`for e in entries`, no slice). `KEEP` exists solely in
  tts-recap.py, so the file is only ever cut back when a recap happens to run.
  Nothing is lost — it grows, it doesn't drop — so this is untidiness, not data
  loss.

  This is the scheduling decision for two items VERDICT.md already records
  separately, *Queue write locking* and *Queue trimming*: **fix the trim as part
  of the locking work, never before it.** Trimming from `enqueue()` today turns
  a bare append into a read-modify-write racing three other writers — a real
  bug in place of a cosmetic one. Trim belongs *inside* the lock.

  One thing that changed since VERDICT was written: `--last N` / `--since S`
  (`rr 3`, `rr 5m`) made entries past the unplayed set **addressable**. Before
  them, everything beyond the recap window was backlog nobody could ask for, so
  where the trim fell was invisible. Now `rr 50` can reach for something a trim
  may already have dropped. Still not data loss — 200 is the stated retention —
  but the boundary is now user-visible, which is an argument for making the cap
  a setting rather than a constant when the locking work lands.
