# Playbook 04 — Obsidian task syntax

**Principle:** When writing TODOs into Obsidian markdown files, **always use checkbox syntax**, never plain bullets.

## The rule

```markdown
- [ ] Task that is not done
- [x] Task that is done
- bullet that is NOT a task (avoid using this for actionable items)
```

Obsidian's Tasks plugin only recognizes `- [ ]` and `- [x]`. Plain `- text` bullets are invisible to task views, query blocks, and dashboards.

## Where this applies

- Any `.md` file in any Obsidian vault.
- Inside daily notes, project files, planning docs, vacation lists, anything.
- Including the cross-agent shared vault if you have access.

## Indentation

Match surrounding indent for nested items:

```markdown
- [ ] Plan trip
  - [ ] Book flights
  - [ ] Book hotel
- [ ] Pack
```

## When to use plain bullets

Plain bullets are fine for **content** (lists of facts, notes, references) — just not for **actions**. If a line is something someone needs to *do*, it's a task and needs `- [ ]`.

## Origin

Helmut asked his agent to add an item to a vacation TODO list. The agent used a plain bullet `- Klavier spielen`. Helmut had to correct it to `- [ ] Klavier spielen` so it would show up in his Tasks view. Locked 2026-05-04.
