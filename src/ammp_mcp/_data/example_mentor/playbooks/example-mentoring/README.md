# Example mentor playbooks

This is the shipped reference corpus that lets `ammp serve` boot, `ammp playbook list` enumerate, and the CLI/test suite exercise the protocol surface on a fresh clone — no setup required.

It is intentionally generic and tiny. To run a real mentor:

1. Create a new directory under `mentors/<your-slug>/`.
2. Drop a `mentor.json` (see `mentors/example/mentor.json` for the schema).
3. Add your real playbook corpus as `playbooks/*.md`.
4. Set `AMMP_DEFAULT_MENTOR=<your-slug>` (or pass `--mentor <your-slug>` on every call).

This `README.md` file inside `playbooks/` is excluded from the corpus by the loader — only sibling `*.md` files become playbooks. Use it for notes about the corpus itself.
