# `playbook` — AMMP

Responsibility: load a mentor's two-level corpus (playbooks → skills), validate caller-supplied ids, and provide cheap substring / keyword search.

The corpus on disk is one of two layouts:

**Plugin-backed (preferred — AgentSkills-aligned):**

```
<mentor_dir>/playbooks/<playbook-slug>/
  playbook.json            # name, description, plugin: "<plugin>@<marketplace>"
```

The loader follows the `plugin:` reference into `<marketplaces_root>/<marketplace>/plugins/<plugin>/skills/<id>/SKILL.md` and reads each skill body from the AgentSkills-format markdown file. `SKILL.md` carries YAML frontmatter (`name`, `description`, `metadata.order`, etc.) — the body's first H1 supplies the `title`.

**Local-only (legacy / standalone):**

```
<mentor_dir>/playbooks/<playbook-slug>/
  playbook.json            # name, description (no plugin: field)
  NN-<skill-id>.md         # one skill per file; NN prefix orders them
  ...
```

* **Playbook** — an *area of practice*. One subdir + `playbook.json`.
* **Skill** — one fine-grained craft rule. Either a `SKILL.md` folder inside a plugin's `skills/` directory or a single `*.md` file inside the playbook subdir.

## Files

- `_service.py` — the `Playbook` + `Skill` dataclasses plus `load_playbooks` (resolves `plugin:` refs against `marketplaces_root`), `flatten_skills`, `safe_id`, `search`, `keyword_rank`.
- `_cli.py` — `playbook_app` (`ammp playbook list / show / search`) and `instruction_app` (`ammp instruction list / show`). `playbook search` invokes the same `_handle_search_playbooks` handler the MCP server exposes as `SearchPlaybooks`, so the shell stays in lockstep with the AMMP wire surface. Auth is bypassed locally.

## Public API (`from ammp_mcp.playbook import …`)

- `Playbook` — dataclass: `id`, `name`, `description`, `dir`, `plugin_ref: tuple[str, str] | None`, `instructions: list[Skill]`.
- `Skill` — dataclass: `id`, `title`, `summary`, `body`, `path`, `playbook_id`, `order: int`.
- `WorkInstruction` — alias of `Skill` re-exported for 0.x backwards compatibility.
- `load_playbooks(playbook_root, marketplaces_root=None)` — scan each subdir for `playbook.json`, resolve any `plugin:` reference against `marketplaces_root` and load skill bodies from the plugin's `skills/<id>/SKILL.md` files (falling back to local `*.md` when no plugin ref is set). Returns `list[Playbook]`.
- `flatten_skills(corpus)` / `flatten_instructions(corpus)` (alias) — single `list[Skill]` across every playbook (used by `AskMentor`).
- `safe_id(raw)` — reject path traversal, dotfiles, oversize ids. Returns the canonical slug or `None`.
- `search(corpus, query, limit)` — substring rank at skill granularity. Returns `(Skill, rank, snippet)` triples.
- `keyword_rank(corpus, question, limit)` — stopword-filtered token rank, for `AskMentor` retrieval. Returns `(Skill, rank)` pairs.

## How callers use it

```python
from ammp_mcp.playbook import load_playbooks, search

corpus = load_playbooks(mentor.playbook_dir, marketplaces_root=settings.marketplaces_root)
hits = search(corpus, "oauth callback", limit=5)
for sk, rank, snippet in hits:
    print(f"{sk.playbook_id}/{sk.id}: {snippet}")
```

## Test coverage

- `tests/unit/test_playbooks.py` — playbook + skill loading (README excluded), metadata parsing, `safe_id` rejection cases, search + keyword_rank at skill granularity, missing-`playbook.json` subdirs skipped.
- `tests/integration/test_server.py` — `ListPlaybooks / GetPlaybook / GetSkill / SearchPlaybooks` over the FastMCP client; `GetPluginArchive` + `/plugins/<name>.zip` route exercised end-to-end.
