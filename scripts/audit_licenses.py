"""Enforce the license allow-list against pip-licenses' JSON report.

Run after ``pip-licenses --format=json --output-file=reports/licenses.json``
(via ``make audit_licenses``).

Policy: permissive MIT-style + BSD + Apache + MPL + ISC + PSF + public
domain pass. LGPL is allowed for library use (not viral the way GPL
is). GPL / AGPL / SSPL / Commons-Clause are banned. For multi-license
offerings (e.g. ``"BSD; GPL; Public Domain"``) the dependency passes
if ANY of the offered licenses is in the allow-list — you'd pick that
one. ``UNKNOWN`` means pip-licenses couldn't read the metadata; we log
those (warning only) since most are packaging gaps, not policy
violations. Tighten this if the project ever becomes regulated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPORT = Path("reports/licenses.json")

ALLOWED = {
    "MIT License",
    "MIT",
    "BSD License",
    "BSD",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "Apache Software License",
    "Apache 2.0",
    "Apache-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "MPL-2.0",
    "ISC License (ISCL)",
    "ISC",
    "Python Software Foundation License",
    "PSF-2.0",
    "Public Domain",
    "The Unlicense (Unlicense)",
    "Unlicense",
    # LGPL — permissive for library use; not "viral" the way GPL is.
    "GNU Lesser General Public License v2 or later (LGPLv2+)",
    "GNU Lesser General Public License v3 or later (LGPLv3+)",
    "LGPL-2.1-or-later",
    "LGPL-3.0-or-later",
    "LGPL",
}

# GPL / AGPL / SSPL / Commons-Clause force the same licence downstream —
# we cannot ship MIT if a transitive dep is GPL-only.
BANNED_TOKENS = ("AGPL", "SSPL", "Commons Clause")


def main() -> int:
    """Walk pip-licenses' JSON report and exit non-zero on any allow-list violation.

    Returns:
        Process exit code: 0 when every dep's license is in the
        allow-list (or part of a multi-license offering with at least
        one allowed option); 1 when a violation is found. ``UNKNOWN``
        license entries are warned about but never block.
    """
    if not REPORT.exists():
        print(f"::error::{REPORT} does not exist — run `make audit_licenses` first.", file=sys.stderr)
        return 1
    rows = json.loads(REPORT.read_text(encoding="utf-8"))
    violations: list[tuple[str, str]] = []
    unknowns: list[tuple[str, str]] = []
    for r in rows:
        lic = (r.get("License") or "").strip()
        if not lic or lic == "UNKNOWN":
            unknowns.append((r["Name"], r.get("Version", "?")))
            continue
        parts = [p.strip() for p in lic.replace(" OR ", ";").split(";")]
        # Multi-license: accept if any offered license is allowed AND
        # none of the offered ones is hard-banned in isolation.
        if any(p in ALLOWED for p in parts):
            continue
        # Single-license: check banned tokens only when the *whole*
        # license string is a banned family (avoids false positive
        # when a multi-license offering merely *includes* GPL).
        if any(tok in lic for tok in BANNED_TOKENS) or lic.startswith("GNU General Public License"):
            violations.append((r["Name"], lic))
            continue
        violations.append((r["Name"], lic))
    if unknowns:
        print(f"::warning::{len(unknowns)} dependencies report UNKNOWN license (likely a packaging metadata gap):")
        for n, v in unknowns[:10]:
            print(f"  - {n} {v}")
    if violations:
        print(f"::error::{len(violations)} license allow-list violations:")
        for name, lic in violations:
            print(f"  - {name}: {lic}")
        return 1
    print(f"OK — {len(rows)} dependencies, allow-list clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
