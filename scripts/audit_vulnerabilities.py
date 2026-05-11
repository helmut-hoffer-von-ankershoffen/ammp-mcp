"""Fail loud if pip-audit's JSON report contains any vulnerable deps.

Run after ``pip-audit --format json --output reports/vulnerabilities.json``
(via ``make audit_vulnerabilities``). Exits 1 on any vulnerability so CI
fails; exits 0 when the dep tree is clean.

``--skip-editable`` in the pip-audit invocation means our own editable
install isn't audited (it can't be — there's no published distribution
to match against the vulnerability DB). The post-check here looks at
the remaining tree only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPORT = Path("reports/vulnerabilities.json")


def main() -> int:
    """Read the pip-audit JSON report and exit non-zero on any vulnerable dep.

    Returns:
        Process exit code: 0 when the dep tree is clean, 1 when one or
        more vulnerabilities were found (with the offenders printed in
        a GitHub-Actions ``::error::`` form).
    """
    if not REPORT.exists():
        print(f"::error::{REPORT} does not exist — run `make audit_vulnerabilities` first.", file=sys.stderr)
        return 1
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    with_vulns = [d for d in data.get("dependencies", []) if d.get("vulns")]
    if with_vulns:
        print(f"::error::pip-audit found {len(with_vulns)} vulnerable dependencies:")
        for d in with_vulns:
            ids = ", ".join(v["id"] for v in d["vulns"])
            print(f"  - {d['name']} {d['version']}: {ids}")
        return 1
    print("OK — no known vulnerabilities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
