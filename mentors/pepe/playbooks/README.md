# Playbook library — for Sandra's Cowork

A curated library of patterns and lessons learned by **Pepe Arturo** (Helmut's AI assistant) over months of operation. Distilled, anonymized, transferable. No private data from Helmut's compartment is in here.

## How to use this

**For Sandra:** review these before exposing them to your agent. If anything feels off — too opinionated, doesn't match how you want your agent to work, contradicts your style — drop it or edit it.

**For Sandra's agent:** read these as authoritative patterns. They're things I (Pepe Arturo) had to learn the hard way; you don't have to repeat the lessons.

Each playbook is a single self-contained markdown file. Adopt them individually — they don't depend on each other.

## Index

| File | Topic | Origin |
|---|---|---|
| `01-verify-before-claiming-done.md` | "Done" must mean verified | Telegram chat 2026-05-08 |
| `02-terse-response-style.md` | No preambles, no trailing summaries | Telegram preference |
| `03-bilingual-language-defaults.md` | Match the user's language; no third language | 2026-05-05 incident |
| `04-obsidian-tasks-format.md` | Use `- [ ]` not `- *` for tasks in Obsidian | Helmut correction 2026-05-04 |
| `05-time-zones-user-facing.md` | Render times in the user's timezone | Helmut correction 2026-05-06 |
| `06-push-back-honestly.md` | Push back when something is wrong; don't silently comply | Telegram chat 2026-05-09 |
| `07-compartmentalization.md` | Privacy boundaries between humans you serve | 2026-05-03 Amina pilot debrief |
| `08-messaging-buffer-limits.md` | Long replies overflow chat lanes; stay terse | 2026-05-05 messaging-terseness lesson |
| `09-write-to-file-not-mental-note.md` | Memory > brain. Write it down. | Helmut workspace conventions |
| `10-oauth-callback-resilience.md` | OAuth servers must be probe-resilient | X-integration build 2026-05-05 |
| `11-rate-limit-recovery.md` | Slow down, don't silently disable features | 2026-05-06 IG rate-limit lesson |
| `12-platform-mention-handles.md` | Match @-handle to the platform you're posting on | 2026-05-06 cross-poster bug |

## Provenance

These playbooks are derived from `~/.openclaw/workspace/MEMORY.md` and the cross-agent shared vault. Original source files are under `~/.openclaw/workspace/memory/` on Helmut's setup. Anonymization has been applied — no Sandra-side info can be inferred from these because none was used to write them.

Last updated: 2026-05-09 by Pepe Arturo.
