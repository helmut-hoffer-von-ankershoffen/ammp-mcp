"""Shared scaffolding for the cross-runtime scenario e2e tests.

Three reusable building blocks:

1. ``_stdio_env`` — env dict that points a freshly-spawned `ammp-mcp` subprocess
   at an isolated tmp_path tree.
2. ``_payload`` — unwrap a FastMCP `CallToolResult` into the structured dict
   the tool returned.
3. ``mock_openclaw_webhook`` — async context manager that runs a tiny
   Starlette app on a free local port and yields the URL. Stand-in for the
   live OpenClaw agent runtime an `openclaw`-backend mentor would POST to.

Scenarios layered on top live in ``tests/e2e/test_scenario_*.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def stdio_env(isolated_tree: Path, **overrides: str) -> dict[str, str]:
    """Build a clean env that points a subprocess at the isolated tree.

    Override any ``AMMP_*`` value via keyword. Anthropic key is always blanked
    so AskMentor falls back to deterministic stub answers unless a mentor
    explicitly configures `backend.kind = openclaw`.
    """
    env = {
        **os.environ,
        # Critical: pin AMMP_DIR to the isolated tree so `ammp serve`'s
        # auto-bootstrap writes its config.env inside the tmp_path, not
        # into the developer's real `~/.ammp/`. Without this, a stdio
        # test contaminates `~/.ammp/config.env` with tmp_path values.
        "AMMP_DIR": str(isolated_tree),
        "AMMP_MENTORS_ROOT": str(isolated_tree / "mentors"),
        "AMMP_MENTEES_FILE": str(isolated_tree / "mentees.json"),
        "AMMP_AUDIT_LOG_PATH": str(isolated_tree / "audit.log"),
        "AMMP_DEFAULT_MENTOR": "pepe",
        "AMMP_REQUIRE_AUTH": "false",
        "AMMP_ANTHROPIC_API_KEY": "",
        # Force HTTP transport selector irrelevant when the spawn uses --stdio,
        # but explicit so the env is reproducible across tests.
        "AMMP_TRANSPORT": "stdio",
    }
    env.update(overrides)
    return env


def payload(result: object) -> dict[str, Any]:
    """Unwrap a FastMCP ``CallToolResult`` into the tool's returned dict.

    The structured `data` attribute is the preferred path; the JSON-encoded
    `content[0].text` is the fallback for tool results that didn't round-trip
    through pydantic.
    """
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if isinstance(text, str):
            return json.loads(text)
    raise AssertionError(f"could not extract dict payload from {result!r}")


# ─── Mock OpenClaw webhook ───────────────────────────────────────────────────


WebhookHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _default_openclaw_handler(req: dict[str, Any]) -> dict[str, Any]:
    """Default: echo question, fixed confidence 0.85."""
    return {
        "answer": f"OpenClaw mentor would say: {req.get('question', '')[:120]}",
        "confidence": 0.85,
    }


@contextlib.asynccontextmanager
async def mock_openclaw_webhook(
    handler: WebhookHandler | None = None,
) -> AsyncIterator[str]:
    """Run a Starlette app on a free local port, yield its base URL.

    The single registered route is ``POST /ammp/ask`` returning the JSON the
    ``handler`` callable produces. Defaults to a fixed-confidence echo.
    """
    handler = handler or _default_openclaw_handler

    async def _endpoint(request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse(handler(body))

    app = Starlette(routes=[Route("/ammp/ask", _endpoint, methods=["POST"])])
    # Pick a free port up-front so the test fixture has a predictable URL
    # before uvicorn binds.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())

    # Wait for the server to claim the port.
    for _ in range(50):
        await asyncio.sleep(0.05)
        if server.started:
            break
    else:
        server.should_exit = True
        await task
        raise RuntimeError(f"mock openclaw webhook never started on :{port}")

    try:
        yield f"http://127.0.0.1:{port}/ammp/ask"
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)


def write_mentor_with_openclaw_backend(
    mentors_root: Path,
    slug: str,
    name: str,
    persona: str,
    confidence_threshold: float,
    webhook_url: str,
    auth_bearer_env: str = "OPENCLAW_BEARER",
) -> None:
    """Rewrite a mentor's ``mentor.json`` so it routes to ``webhook_url``.

    Used by scenarios that need a real (mock-backed) OpenClaw-routed mentor
    rather than the deterministic stub. The mentor directory + playbooks must
    already exist (typically from the ``isolated_tree`` fixture).
    """
    mentor_dir = mentors_root / slug
    mentor_dir.mkdir(parents=True, exist_ok=True)
    (mentor_dir / "mentor.json").write_text(
        json.dumps(
            {
                "name": name,
                "persona": persona,
                "confidence_threshold": confidence_threshold,
                "backend": {
                    "kind": "openclaw",
                    "url": webhook_url,
                    "auth_bearer_env": auth_bearer_env,
                    "timeout_seconds": 5.0,
                    "max_concurrent": 4,
                },
            }
        ),
        encoding="utf-8",
    )
