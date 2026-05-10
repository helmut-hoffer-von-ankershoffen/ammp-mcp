# `playbook` — AMMP

Responsibility: load a mentor's curated markdown corpus, validate caller-supplied playbook ids, and provide cheap substring / keyword search.

## Files

- `_service.py` — the `Playbook` dataclass plus `load_corpus`, `safe_id`, `search`, `keyword_rank`. Also contains `_load_one` (package-private, imported lazily by `server._handle_get_playbook` to avoid widening the public surface).
- `_cli.py` — `playbook_app` Typer subgroup with `ammp playbook list / show`.

## Public API (`from ammp_mcp.playbook import …`)

- `Playbook` — dataclass: `id`, `title`, `summary`, `body`, `path`.
- `load_corpus(directory)` — scan `*.md` (excluding `README.md`), return `list[Playbook]` sorted by filename.
- `safe_id(raw)` — reject path traversal, dotfiles, oversize ids. Returns the canonical slug or `None`.
- `search(corpus, query, limit)` — substring rank with snippets, for `SearchPlaybooks`.
- `keyword_rank(corpus, question, limit)` — stopword-filtered token rank, for `AskMentor` retrieval.

## How callers use it

```python
from ammp_mcp.playbook import load_corpus, search

corpus = load_corpus(mentor.playbook_dir)
hits = search(corpus, "oauth callback", limit=5)
```

## Test coverage

- `tests/unit/test_playbooks.py` — corpus loading (README excluded), title/summary extraction, `safe_id` rejection cases, search + keyword_rank.
- `tests/integration/test_server.py` — `ListPlaybooks / GetPlaybook / SearchPlaybooks` over the FastMCP client.
