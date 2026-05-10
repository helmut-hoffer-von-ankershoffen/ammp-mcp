# `mentee` — AMMP

Responsibility: describe what a mentee is (pydantic model) and manage the allowlist that gates incoming Bearer keys.

## Files

- `_models.py` — `Mentee` pydantic class. Stores only the SHA-256 of an API key; plaintext is never on disk.
- `_service.py` — JSON load/save of the allowlist plus the `hash_api_key` / `find_mentee_by_api_key` helpers. SHA-256 is the right choice here because keys are 256 bits of CSPRNG output — a slow KDF buys nothing.
- `_cli.py` — `mentee_app` Typer subgroup with `list / add / remove / rotate-key / check-key`. `add` and `rotate-key` are the only places a plaintext key surfaces — they print once and never persist it.

## Public API (`from ammp_mcp.mentee import …`)

- `Mentee` — pydantic class.
- `load_mentees(path)`, `save_mentees(path, mentees)` — JSON round-trip.
- `hash_api_key(plaintext)` — SHA-256 hex digest used both to write `api_key_hash` and to compare incoming requests.
- `find_mentee_by_api_key(mentees, plaintext)` — auth check; returns `Mentee | None`.

## How callers use it

```python
from ammp_mcp.mentee import load_mentees, find_mentee_by_api_key

mentees = load_mentees(settings.mentees_file)
caller = find_mentee_by_api_key(mentees, bearer_token)
```

## Test coverage

- `tests/unit/test_registries.py` — load/save, hash determinism, allowlist lookup.
- `tests/unit/test_models.py` — `Mentee` validation.
- `tests/integration/test_cli.py` — full add/remove/rotate-key/check-key round trip.
