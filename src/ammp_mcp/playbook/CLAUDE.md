# `playbook` — AMMP

Responsibility: load a mentor's two-level corpus (playbooks → work instructions), validate caller-supplied ids, and provide cheap substring / keyword search.

The corpus on disk is:

```
<mentor_dir>/playbooks/<playbook-slug>/
  playbook.json            # name, description
  <instruction-id>.md      # one work instruction per file
  ...
```

* **Playbook** — an *area of practice*. One subdir + `playbook.json`.
* **WorkInstruction** — one fine-grained craft rule. One markdown file inside a playbook subdir.

## Files

- `_service.py` — the `Playbook` + `WorkInstruction` dataclasses plus `load_playbooks`, `flatten_instructions`, `safe_id`, `search`, `keyword_rank`.
- `_cli.py` — `playbook_app` (`ammp playbook list / show / search`) and `instruction_app` (`ammp instruction list / show`). `playbook search` invokes the same `_handle_search_playbooks` handler the MCP server exposes as `SearchPlaybooks`, so the shell stays in lockstep with the AMMP wire surface. Auth is bypassed locally.

## Public API (`from ammp_mcp.playbook import …`)

- `Playbook` — dataclass: `id`, `name`, `description`, `dir`, `instructions: list[WorkInstruction]`.
- `WorkInstruction` — dataclass: `id`, `title`, `summary`, `body`, `path`, `playbook_id`.
- `load_playbooks(playbook_root)` — scan each subdir for `playbook.json`, load each playbook with its `*.md` instructions. Returns `list[Playbook]`.
- `flatten_instructions(corpus)` — single `list[WorkInstruction]` across every playbook (used by `AskMentor`).
- `safe_id(raw)` — reject path traversal, dotfiles, oversize ids. Returns the canonical slug or `None`.
- `search(corpus, query, limit)` — substring rank at instruction granularity. Returns `(WorkInstruction, rank, snippet)` triples.
- `keyword_rank(corpus, question, limit)` — stopword-filtered token rank, for `AskMentor` retrieval. Returns `(WorkInstruction, rank)` pairs.

## How callers use it

```python
from ammp_mcp.playbook import load_playbooks, search

corpus = load_playbooks(mentor.playbook_dir)
hits = search(corpus, "oauth callback", limit=5)
for wi, rank, snippet in hits:
    print(f"{wi.playbook_id}/{wi.id}: {snippet}")
```

## Test coverage

- `tests/unit/test_playbooks.py` — playbook + instruction loading (README excluded), metadata parsing, `safe_id` rejection cases, search + keyword_rank at instruction granularity, missing-`playbook.json` subdirs skipped.
- `tests/integration/test_server.py` — `ListPlaybooks / GetPlaybook / GetWorkInstruction / SearchPlaybooks` over the FastMCP client.
