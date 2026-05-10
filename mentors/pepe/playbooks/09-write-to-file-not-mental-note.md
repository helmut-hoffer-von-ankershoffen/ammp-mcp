# Playbook 09 — Memory > brain. Write it down.

**Principle:** "Mental notes" don't survive session restarts. Files do. When something matters, write it to a file.

## The pattern

Every AI agent wakes up fresh each session. Whatever you "remembered" in conversation is gone unless it's in:

1. A file in a workspace folder you re-read on startup
2. A long-term memory file (`MEMORY.md` or equivalent) that's loaded as context
3. A vault that's synced into your session context

If you tell a user "I'll remember that for next time" without writing it down, you're lying.

## When to write

- User makes a correction → write it
- User states a preference → write it
- User shares a recurring fact (kids' names, schedule, allergies) → write it
- User makes a decision and you understand the reason → write both
- You discover a non-obvious workaround → write it
- You make a mistake → write what you learned so future-you doesn't repeat it

## Where to write

| Type | Where |
|---|---|
| Long-term curated facts about the user | `MEMORY.md` |
| Daily raw events | `daily/YYYY-MM-DD.md` |
| Tool / integration notes | `TOOLS.md` |
| Skill-specific learnings | inside the relevant skill |
| Cross-agent transferable lessons | shared vault under your `<Agent Name>/Notes/` |

## What NOT to write

- Secrets (passwords, account numbers, recovery phrases) — these go in a credential store, never in MEMORY
- Information from another person's compartment
- Speculation framed as fact

## The discipline

The temptation is always to think "I'll remember this naturally." You won't. Write it down within the same turn the user said it. Five seconds of writing > ten minutes of confused future-you.

## Origin

OpenClaw / Helmut's workspace conventions. The `AGENTS.md` file in his workspace literally says: *"Mental notes don't survive session restarts. Files do. Text > Brain."*
