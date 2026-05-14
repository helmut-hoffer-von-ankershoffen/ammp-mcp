"""Packaged data — the shipped example mentor.

Lives inside the importable package so it ships with every install
(pip, uvx, dev clone) and so a fresh `~/.ammp/` install has something
to point at without any extra steps.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path


def example_mentor_path() -> Path:
    """Path to the shipped example mentor's directory.

    Returns the resolved filesystem path to ``src/ammp_mcp/_data/example_mentor/``.
    Callers (typically ``ammp setup``) copy this tree into the operator's
    ``~/.ammp/mentors/example/`` to bootstrap a fresh install.
    """
    return Path(str(resources.files(__package__ or "ammp_mcp._data").joinpath("example_mentor")))


def favicon_path() -> Path:
    """Path to the shipped favicon directory (the helmguild compass).

    Contains ``favicon.svg``, ``favicon-32.png`` and
    ``apple-touch-icon.png`` — copies of the same files served from
    ``www.helmguild.com``. Served by ``ammp serve`` so deployments
    under ``mcp.helmguild.com`` show the brand favicon in browser tabs
    without hot-linking to the marketing site.

    Returns:
        Filesystem path to ``src/ammp_mcp/_data/favicon/``.
    """
    return Path(str(resources.files(__package__ or "ammp_mcp._data").joinpath("favicon")))


def desktop_bundle_path() -> Path:
    """Path to the Claude Desktop bundle template directory.

    Contains ``manifest.json.template`` and ``icon.png``. Used by
    :func:`ammp_mcp.server._build_desktop_bundle` to assemble a
    ``.mcpb`` archive on the fly when a visitor downloads the bundle
    from ``GET <prefix>/desktop-bundle.mcpb``.

    Returns:
        Filesystem path to ``src/ammp_mcp/_data/desktop_bundle/``.
    """
    return Path(str(resources.files(__package__ or "ammp_mcp._data").joinpath("desktop_bundle")))
