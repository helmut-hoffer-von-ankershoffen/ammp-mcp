# `examples/` — operator-facing reference templates

Annotated copies of every file format `ammp-mcp` reads at runtime.
They exist so a new operator can answer "what does a `mentor.json`
look like?" without grepping the source.

| File | What it is |
|---|---|
| [`mentor.example.json`](mentor.example.json) | Schema for `<AMMP_DIR>/mentors/<slug>/mentor.json` — name, persona, confidence threshold, backend block. Inline `_doc` / `_about_*` keys annotate every field; the wizard strips them on save. |
| [`playbook.example.md`](playbook.example.md) | The markdown shape `<mentor>/playbooks/*.md` files have to be in: title heading, summary line, body, optional YAML frontmatter. Includes the loader's filename → id mapping rule. |
| [`mentees.example.json`](mentees.example.json) | Shape of the Bearer-key allowlist (one entry per mentee — slug, operator, runtime, SHA-256 of the API key, per-minute rate budget). |

## Where the live versions live

These files are **references**, not bootstrap data. The actual default
mentor that `ammp serve` copies into `~/.ammp/mentors/example/` on
first run lives as **package data** at
`src/ammp_mcp/_data/example_mentor/`. Two concerns, two locations:

- `examples/` — what you read when documenting / writing a new mentor.
- `src/ammp_mcp/_data/example_mentor/` — what the wheel ships and the
  bootstrap consumes.

If you'd like to add your own mentor, the wizard path is

```bash
cp -r ~/.ammp/mentors/example ~/.ammp/mentors/<your-slug>
$EDITOR ~/.ammp/mentors/<your-slug>/mentor.json
$EDITOR ~/.ammp/mentors/<your-slug>/playbooks/*.md
```
