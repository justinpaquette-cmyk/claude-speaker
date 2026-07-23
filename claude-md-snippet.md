
## Spoken Summary (TTS)

A Stop hook speaks responses aloud via macOS `say`. End every substantive response with a final line `🔊 <one to three short spoken sentences>` — plain conversational English, no markdown, no paths, no code; capture the actual outcome, not just "done." The hook speaks only that line (trimmed to the configurable summary length, default 1200 chars via `/tts length`); without the line, it reads a sanitized fallback trimmed to the same cap. Skip the line for trivial one-word replies.
