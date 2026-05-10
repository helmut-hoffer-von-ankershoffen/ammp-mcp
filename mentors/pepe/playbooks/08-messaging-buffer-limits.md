# Playbook 08 — Messaging buffer limits on chat platforms

**Principle:** Long replies on chat platforms (Telegram, WhatsApp, iMessage bridges) can silently fail. Stay terse, not because of style preference but because of infrastructure.

## The pattern

Some AI-to-chat bridges have a stdout buffer cap. If your reply (including any tool-output noise that leaked through) exceeds it, the **entire reply is dropped silently** — the user sees nothing, you see "sent successfully," and the conversation just... breaks.

Symptoms:
- User says "hello?" and you have no idea why
- Your verbose answer never lands
- Logs show success on your side, silence on theirs

## How to mitigate

1. **Cap your replies short.** A few hundred words max for chat. If the answer is longer, summarize and offer to send the full thing as a file.
2. **Don't process media files inline on chat lanes.** mp4 transcripts, large audio, base64-embedded images — route to a TUI or local file. Reply with a short summary.
3. **Don't paste large tool output back into chat.** Inspect, distill, share the conclusion.

## Format for "I have a long answer"

> "Short version: <one paragraph>. Want the full breakdown? I can drop it to a file at <path> or send it section by section."

## Anti-pattern

User asks *"summarize this 30-min meeting recording."*
- ❌ Agent transcribes the whole thing into the reply, hits buffer cap, user sees nothing.
- ✅ Agent transcribes to a file, replies with 5-bullet summary + file path.

## Origin

Locked 2026-05-05 after long Telegram replies started disappearing for Helmut. Documented in OpenClaw memory as "feedback_messaging_terseness" and "feedback_no_media_processing_on_chat_lanes."
