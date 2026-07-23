
## Spoken Summary (TTS)

A Stop hook speaks responses aloud via macOS `say`. End every substantive response with a final line `🔊 <spoken summary>` — plain conversational English, no markdown, no paths, no code; capture the actual outcome, not just "done." **Scale the line to the turn:** one sentence for a quick reply, up to a short paragraph for a big multi-step turn. The hook speaks only that line (trimmed to the summary length — adaptive to turn size by default, or a flat cap set via `/tts length`); without the line, it reads a sanitized fallback trimmed to the same cap. Skip the line for trivial one-word replies.
