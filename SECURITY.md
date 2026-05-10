# Security Policy

## Reporting Security Issues

If you discover a security vulnerability in `ammp-mcp`, please [report it via GitHub Security Advisories](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/security/advisories/new) — that creates a private channel between reporter and maintainers, and lets us coordinate a fix and disclosure timeline.

We take all security reports seriously. Upon receiving one we will:

1. Confirm receipt of the report.
2. Investigate the issue.
3. Work on a fix.
4. Release a security update and credit the reporter (unless they prefer otherwise).

## Supported Versions

`ammp-mcp` is in early-development (v0.x). We provide security updates for the latest minor version only. Once v1.0 ships, that policy is revisited.

## Threat surface

`ammp-mcp` is a reference implementation of the AMMP Mentoring track. The protocol's normative invariants drive the security model:

- **Compartmentalisation (AMMP §3.3).** The mentor never accumulates a profile of the mentee. The audit log is hash-only — payloads are never written in plaintext. A successful breach of the audit file does not leak request content.
- **Human-Gated Escalation (AMMP §3.4).** `EscalateToHuman` returns *guidance text* the mentee hands to its own operator. The server has no mechanism to reach across compartments.
- **No retention.** The server holds nothing about a request after returning the response (no DB, no cache layer, no transcript).

Mentee authentication is by **SHA-256-hashed Bearer keys** kept in `mentees.json`. The plaintext key is shown to the operator *once* when minted by the `ammp add-mentee` CLI; only the hash is written to disk. A leak of `mentees.json` does not expose live keys.

The `AskMentor` LLM backend is bounded-concurrency (`asyncio.Semaphore`) and the only egress dependency is the Anthropic Messages API.

## Automated Security Analysis

`ammp-mcp` uses several automated tools to monitor and improve security posture continuously.

### 1. Vulnerability Scanning

a. **[GitHub Dependabot](https://github.com/dependabot)** — monitors dependencies for vulnerabilities pre- and post-release. [Dependabot alerts](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/security/dependabot).

b. **[pip-audit](https://pypi.org/project/pip-audit/)** — runs in CI and on every push / PR / weekly schedule against the [Python Advisory Database](https://github.com/pypa/advisory-database). `vulnerabilities.json` published as a workflow artefact and (when shipping a release) attached to [GitHub Releases](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/releases).

c. **[Trivy](https://trivy.dev/latest/)** — scans the source tree for vulnerabilities using data from the [GitHub Advisory Database](https://github.com/advisories?query=ecosystem%3Apip) and [OSV.dev](https://osv.dev/list?q=&ecosystem=PyPI). SARIF results upload to [GitHub Code Scanning](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/security/code-scanning).

### 2. License Compliance and SBOM

a. **[pip-licenses](https://pypi.org/project/pip-licenses/)** — audits all transitive dependency licenses against an allow-list. `licenses.csv`, `licenses.json` published as workflow artefacts.

b. **[cyclonedx-py](https://github.com/CycloneDX/cyclonedx-python)** — generates a Software Bill of Materials in [CycloneDX](https://cyclonedx.org/) format. `sbom.json` published as a workflow artefact.

c. **[Trivy](https://trivy.dev/latest/)** — also generates an SPDX-format SBOM as `sbom.spdx`.

### 3. Static Code Analysis

a. **[GitHub CodeQL](https://codeql.github.com/)** — runs on every push / PR / weekly schedule. [Code scanning results](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/security/code-scanning).

b. **[Ruff `S` rules](https://docs.astral.sh/ruff/rules/#flake8-bandit-s)** — bandit-style security lint integrated into the standard `ruff check` step in CI.

c. **[mypy strict](https://mypy.readthedocs.io/)** — strict type-checking surfaces classes of memory and contract bugs that pure runtime testing can miss.

### 4. Secret Detection

a. **[GitHub Secret scanning](https://docs.github.com/en/code-security/secret-scanning/introduction/about-secret-scanning)** — automatic on public repos. [Secret scanning alerts](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/security/secret-scanning).

b. **[`detect-private-key`](https://github.com/pre-commit/pre-commit-hooks)** — pre-push hook stops accidental private-key commits. See [`.pre-commit-config.yaml`](.pre-commit-config.yaml).

### 5. Build Integrity

a. **[SLSA build provenance](https://slsa.dev/)** — every release tag generates a Sigstore-attested provenance statement for each `dist/` artefact. See [`.github/workflows/release.yml`](.github/workflows/release.yml).

b. **[PyPI trusted publishing (OIDC)](https://docs.pypi.org/trusted-publishers/)** — releases publish to PyPI without long-lived API tokens.

c. **SHA256SUMS** — every release ships a `SHA256SUMS` file alongside the dist artefacts.

## Security Best Practices

The project follows these practices:

1. Regular dependency updates via Dependabot.
2. Comprehensive test coverage (unit + integration + e2e), with security-critical paths (auth, hashing, audit log, path-traversal guards) covered by explicit tests.
3. Code review for changes by external contributors.
4. Pre-push hook + CI gate for ruff, ruff-format, mypy.
5. Adherence to Python security best practices (no `eval`, no `pickle` over the wire, parameterised queries when applicable, strict input validation at protocol boundaries).

## Security Compliance

For questions about security compliance or our security practices, please open a [GitHub Security Advisory](https://github.com/helmut-hoffer-von-ankershoffen/ammp-mcp/security/advisories/new) (preferred) or email helmuthva@gmail.com.
