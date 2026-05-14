"""Verify every ```mermaid``` block in the README renders cleanly.

This catches the class of regression where someone edits a sequence /
graph diagram, GitHub renders it as-is, and a parse error there ships
silently. We extract each fenced ``mermaid`` block from the README,
hand it to ``mmdc`` (the official mermaid-cli renderer that ships
through npm), and assert each one produces a non-empty PNG.

Skipped automatically when ``mmdc`` is missing from PATH (typical on
CI without Node + the mermaid-cli npm install).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

README = Path(__file__).resolve().parent.parent.parent / "README.md"
MMD_FENCE = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)


def _have_mmdc() -> bool:
    return shutil.which("mmdc") is not None


def _extract_blocks() -> list[str]:
    text = README.read_text(encoding="utf-8")
    return [m.group(1) for m in MMD_FENCE.finditer(text)]


def test_readme_contains_at_least_four_mermaid_diagrams() -> None:
    """We currently ship four diagrams (component + 3 sequences).

    Pinned so a refactor that accidentally drops a diagram from the
    README is caught before it lands on PyPI.
    """
    blocks = _extract_blocks()
    assert len(blocks) >= 4, f"expected ≥4 mermaid blocks, got {len(blocks)}"


@pytest.mark.parametrize("idx", range(8))  # generous upper bound
def test_readme_mermaid_block_renders(idx: int) -> None:
    """Each ```mermaid``` block in README.md renders to a non-empty PNG."""
    if not _have_mmdc():
        pytest.skip("mmdc (mermaid-cli) not on PATH; install via `npm i -g @mermaid-js/mermaid-cli`")
    blocks = _extract_blocks()
    if idx >= len(blocks):
        pytest.skip(f"only {len(blocks)} blocks; index {idx} OOB")
    with tempfile.TemporaryDirectory(prefix="mmd-") as tmpdir:
        tmp = Path(tmpdir)
        src = tmp / f"d{idx}.mmd"
        out = tmp / f"d{idx}.png"
        src.write_text(blocks[idx], encoding="utf-8")
        proc = subprocess.run(
            ["mmdc", "-i", str(src), "-o", str(out), "-b", "transparent"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # mmdc writes to stderr even on success; assert by file existence + non-empty.
        assert out.is_file(), (
            f"mermaid block {idx} failed to render\n--- stderr ---\n{proc.stderr}\n--- source ---\n{blocks[idx]}"
        )
        assert out.stat().st_size > 1024, f"rendered PNG suspiciously small ({out.stat().st_size} bytes)"
