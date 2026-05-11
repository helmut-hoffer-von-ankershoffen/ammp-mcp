# Contributing to `ammp-mcp`

Welcome. This file collects everything that's *not* obvious from poking at `make`, `pyproject.toml`, or `README.md` alone.

## Quick start (clone → working venv)

```bash
git clone https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp
cd ammp-mcp

# Use uv (fastest, recommended) or fall back to python -m venv + pip:
uv sync --all-extras                   # uv path — builds .venv from pyproject.toml
# OR
python3.13 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

# One-time hook install. Catches ruff problems before they reach CI.
uv run pre-commit install --hook-type pre-push
```

## Running things

```bash
uv run ammp --help                     # housekeeping CLI (mentors, mentees, status, usage)
uv run ammp setup                      # first-run installation wizard
uv run ammp status                     # validate the current install
uv run ammp usage --days 7             # aggregate the audit log for the last week
uv run ammp serve                      # boot the MCP server (alias for `ammp system serve`)

uv run pytest -m unit                  # offline solitary tests
uv run pytest -m integration           # in-memory FastMCP client + Typer CliRunner
uv run pytest -m e2e                   # hits real Claude API (needs AMMP_ANTHROPIC_API_KEY)

uv run ruff check . && uv run ruff format --check .
uv run mypy src/ammp_mcp
```

## Repo-side secrets you (the maintainer) need to set once

The CI workflows reference three GitHub repo secrets. Until each is set, the corresponding feature is silently skipped or the badge stays empty.

| Secret | What it unlocks | How to get it |
|---|---|---|
| `CODECOV_TOKEN` | Filled-in **Coverage** badge + per-PR coverage diff. Without it, codecov rejects uploads on protected branches with `"Token required because branch is protected"`. | Activate the project at <https://app.codecov.io/gh/helmut-hoffer-von-ankershoffen/ammp-mcp> → copy the upload token → `gh secret set CODECOV_TOKEN --repo helmut-hoffer-von-ankershoffen/ammp-mcp`. |
| `SONAR_TOKEN` | Filled-in **Quality Gate / Security / Maintainability** badges + the SonarCloud project page. | Provision at <https://sonarcloud.io> (org `helmut-hoffer-von-ankershoffen`, project key `helmut-hoffer-von-ankershoffen_ammp-mcp` per `sonar-project.properties`) → generate a token at <https://sonarcloud.io/account/security> → `gh secret set SONAR_TOKEN --repo helmut-hoffer-von-ankershoffen/ammp-mcp`. |
| PyPI **trusted publisher** | `release.yml` can publish to PyPI on `vX.Y.Z` tags. | One-time configure at <https://pypi.org/manage/account/publishing/> with project `ammp-mcp`, owner `helmut-hoffer-von-ankershoffen`, repo `ammp-mcp`, workflow `release.yml`, env `pypi`. No GitHub secret needed — uses OIDC. |

Until those are configured, CI still passes; the badges and PyPI publish step degrade gracefully (the release workflow's PyPI job is `continue-on-error` so the GitHub Release still ships).

## Regenerating ATTRIBUTIONS.md

After bumping any dependency:

```bash
uv run python scripts/generate_attributions.py
git add ATTRIBUTIONS.md
git commit -m "Refresh ATTRIBUTIONS.md after dep bump"
```

The audit workflow regenerates `reports/licenses.json` on every push, but the human-readable `ATTRIBUTIONS.md` is committed so casual readers don't have to download an artefact.

## Making a release

1. Bump `pyproject.toml [project].version`.
2. Update `CHANGELOG.md` if you keep one (the release workflow auto-generates notes from commits when it's missing).
3. Tag and push:

   ```bash
   git tag -a v0.3.0 -m "v0.3.0 — what changed"
   git push origin v0.3.0
   ```

   The `Release` workflow handles the rest: tag/version preflight, test gate, sdist + wheel build with SLSA/Sigstore provenance, SHA256SUMS, PyPI publish (OIDC trusted publisher), GitHub Release with the artefacts attached.

## House style

- **Calm operator register.** Comments and docstrings explain *why*, not *what* — the names already say what.
- **No emojis in code or docs.** (Badges in `README.md` are an exception — they're third-party SVGs.)
- **Pre-push gate is strict.** If `ruff` or `ruff format --check` complains, fix it before pushing — don't `--no-verify`.

## Conduct

This repo follows the [Contributor Covenant](https://www.contributor-covenant.org/) — be kind, assume good faith, push back honestly when something seems wrong. Disagreements are resolved by argument, not by seniority.

## Where to ask

For non-security questions, open a discussion / issue at <https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/issues>. For security-sensitive reports, see [`SECURITY.md`](SECURITY.md).
