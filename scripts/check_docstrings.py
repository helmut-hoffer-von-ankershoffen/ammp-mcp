"""Fail loud when any function in ``src/`` is missing a docstring.

Ruff's `D103` only checks PUBLIC functions; pydoclint only checks
docstring CONTENT (Args/Returns/Raises match the signature). Neither
catches a private helper with no docstring at all. This script does
exactly that — a thin AST walk that exits non-zero if it finds any
function (sync or async, private or public, top-level or nested)
without a docstring.

Run locally with:

    uv run python scripts/check_docstrings.py

Wired into pre-commit and CI alongside ruff + mypy + pydoclint.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "ammp_mcp"


def _find_undocumented(path: Path) -> list[tuple[Path, int, str]]:
    """Return ``[(path, line, name)]`` for every undocumented function in one file.

    Walks the AST so we catch nested functions and methods, not just
    top-level defs. Skips an ``__init__`` whose containing class has
    its own docstring — Google's convention is that constructor
    arguments are documented in either the class docstring OR the
    ``__init__`` docstring (never both), so a missing ``__init__``
    docstring is fine as long as the class has one.

    Args:
        path: Path to the Python source file to scan.

    Returns:
        Findings as ``(path, line_number, function_name)`` tuples — one
        per undocumented function. Empty list when every function in
        the file is documented.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    findings: list[tuple[Path, int, str]] = []
    # Map id(FunctionDef) -> True when the function lives in a class
    # whose own docstring is non-empty; an undocumented `__init__` is
    # tolerated in that case.
    init_inherits_class_doc: set[int] = set()
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef) and (ast.get_docstring(cls) or ""):
            for body_node in cls.body:
                if isinstance(body_node, ast.FunctionDef | ast.AsyncFunctionDef) and body_node.name == "__init__":
                    init_inherits_class_doc.add(id(body_node))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if ast.get_docstring(node):
            continue
        if id(node) in init_inherits_class_doc:
            continue
        findings.append((path, node.lineno, node.name))
    return findings


def main() -> int:
    """Walk ``src/ammp_mcp`` and exit 1 if any function lacks a docstring.

    Returns:
        Process exit code: 0 when every function is documented, 1
        otherwise (with offenders printed to stderr).
    """
    misses: list[tuple[Path, int, str]] = []
    for f in sorted(ROOT.rglob("*.py")):
        misses.extend(_find_undocumented(f))
    if misses:
        print(f"{len(misses)} undocumented function(s):", file=sys.stderr)
        for p, lineno, name in misses:
            print(f"  {p.relative_to(ROOT.parent.parent)}:{lineno}: {name}", file=sys.stderr)
        print("\nEvery function (private or public) must carry a Google-style", file=sys.stderr)
        print("docstring. Add one and re-run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
