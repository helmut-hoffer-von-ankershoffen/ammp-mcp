"""Fail loud if pip-audit's JSON report contains any vulnerable deps.

Run after ``pip-audit --format json --output reports/vulnerabilities.json``
(via ``make audit_vulnerabilities``). Exits 1 on any vulnerability so CI
fails; exits 0 when the dep tree is clean.

``--skip-editable`` in the pip-audit invocation means our own editable
install isn't audited (it can't be — there's no published distribution
to match against the vulnerability DB). The post-check here looks at
the remaining tree only.

Disputed advisories with no upstream fix can be ignored via
``IGNORED_VULNS`` — each entry needs a one-line justification, and any
addition is reviewed in the same PR that adds it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPORT = Path("reports/vulnerabilities.json")

# Vulnerability IDs we explicitly accept the risk of, with rationale.
# Reviewed when an entry lands here; re-evaluated each time pip-audit
# surfaces the ID again (the printed notice keeps it visible in CI logs).
IGNORED_VULNS: dict[str, str] = {
    "PYSEC-2025-183": (
        "CVE-2025-45768 — pyjwt 'weak encryption' advisory disputed by "
        "upstream (pyca/pyjwt): the key length is the caller's choice, "
        "the library does not pick weak keys. ammp-mcp signs/verifies "
        "bearer tokens with caller-generated keys at HS256 strength or "
        "above. Re-evaluate if upstream withdraws the dispute or ships "
        "a fix that changes the public API."
    ),
}


def evaluate(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    """Split a pip-audit report into (failing, ignored).

    Args:
        report: parsed ``pip-audit --format json`` output.

    Returns:
        ``(failing, ignored)``: ``failing`` is the list of dependency
        records that still have at least one un-ignored vuln; ``ignored``
        is the flat list of ``(pkg_name, pkg_version, vuln_id)`` tuples
        the policy excluded, so they can be logged for traceability.
    """
    failing: list[dict[str, Any]] = []
    ignored: list[tuple[str, str, str]] = []
    for dep in report.get("dependencies", []):
        kept = []
        for vuln in dep.get("vulns", []) or []:
            if vuln["id"] in IGNORED_VULNS:
                ignored.append((dep["name"], dep["version"], vuln["id"]))
            else:
                kept.append(vuln)
        if kept:
            failing.append({"name": dep["name"], "version": dep["version"], "vulns": kept})
    return failing, ignored


def main() -> int:
    """Read the pip-audit JSON report and exit non-zero on any un-ignored vuln.

    Returns:
        Process exit code: 0 when the dep tree is clean (or only carries
        IGNORED_VULNS entries), 1 when one or more un-ignored
        vulnerabilities were found (offenders printed in GitHub-Actions
        ``::error::`` form).
    """
    if not REPORT.exists():
        print(f"::error::{REPORT} does not exist — run `make audit_vulnerabilities` first.", file=sys.stderr)
        return 1
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    failing, ignored = evaluate(data)

    for name, version, vuln_id in ignored:
        print(f"::notice::pip-audit: ignoring {name} {version} {vuln_id} — {IGNORED_VULNS[vuln_id]}")

    if failing:
        print(f"::error::pip-audit found {len(failing)} vulnerable dependencies:")
        for d in failing:
            ids = ", ".join(v["id"] for v in d["vulns"])
            print(f"  - {d['name']} {d['version']}: {ids}")
        return 1
    print("OK — no known vulnerabilities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
