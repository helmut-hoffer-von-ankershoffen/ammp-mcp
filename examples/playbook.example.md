# Playbook NN — Short imperative title

**Principle:** One load-bearing sentence that captures the rule. Keep it short. The mentee reads this first when SearchPlaybooks returns the playbook as a candidate; it's the part that has to stand alone.

## When this applies

Brief description of the situations the playbook covers. Bullet list works; prose works. A mentor that grounds an AskMentor answer in this playbook will quote from here, so write for an agent's reading — concrete signals, no marketing.

## How to apply

1. Step one — concrete enough to act on.
2. Step two — what to check, what to record, what to escalate.
3. Step three — the failure mode you most want to prevent.

## What this is not

(Optional) Common misreads to head off. "Not a fallback path", "not about tone", "not a substitute for X" — whatever framing you have learned saves you the most rework.

---

## File format notes (the part the loader cares about)

- **Filename = id**: The first part of the filename (the stem, before
  `.md`) is the AMMP `id` used by `GetPlaybook(id=...)`. Kebab-case
  ASCII is the convention: `01-cite-or-decline.md` → id `01-cite-or-decline`.
- **First `# ` heading is the title**: surfaced in ListPlaybooks /
  ListMentors as the human-readable name. Falls back to the filename
  stem when no `# ` heading is present.
- **Summary**: the first non-heading line after the title. Truncated to
  ~200 chars when long. This is what `SearchPlaybooks` shows in its
  ranked-match table.
- **Body**: the full file content (including this frontmatter-style
  `## When this applies` section) is what `GetPlaybook` returns and
  what `AskMentor` pastes into the backend's system prompt as
  grounding context.
- **YAML frontmatter is tolerated**: a leading `---\n...\n---\n` block
  is stripped before title/summary extraction. Use it for editor
  metadata; the AMMP loader ignores anything inside.
- **A `README.md` in the same `playbooks/` directory is NOT loaded as a
  playbook** — use it for human-only notes about the corpus.
