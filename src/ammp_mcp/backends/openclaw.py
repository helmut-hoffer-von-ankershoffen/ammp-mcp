"""OpenClaw-routed mentor backend.

Posts the question to a configured HTTP endpoint hosted by an OpenClaw
runtime (or any other system that implements the wire contract below).
The receiving service is expected to forward the question into a live
agent session — for Pepe Arturo, that's the actual Pepe-on-OpenClaw,
who answers with full conversational context (memory, vault, prior
decisions) instead of as a stateless persona.

Wire contract (the receiving service MUST implement this):

    POST <url>
    Content-Type: application/json
    Authorization: Bearer <token>   # if auth_bearer_env is configured

    Request body:
    {
      "mentor": "<slug>",          // mentor slug
      "name": "<display name>",    // display name
      "persona": "...",            // mentor's persona string
      "question": "...",           // mentee's question
      "playbooks": [               // top-N retrieved playbooks
        {"title": "...", "body": "..."},
        ...
      ],
      "ammp_version": "<x.y.z>",
      "ammp_draft": "draft-ammp-01"
    }

    Response 200:
    {
      "answer": "...",             // string, 3-6 sentences in the persona's voice
      "confidence": 0.0..1.0       // float, mentor-self-reported
    }

    Response on transient failure: any non-200 status. The backend
    will surface ``llm_failed`` to the mentee in-band; the server
    handles fall-back semantics.

The contract is intentionally generic enough that other backends
(Hermes-on-someone-else's-runtime, a second Claude Cowork mentor, a
Synthesia avatar, etc.) can implement it without ammp-mcp needing to
know about them.
"""

from __future__ import annotations

import logging
import os

import httpx

from .. import __ammp_draft__, __version__
from .anthropic import parse_envelope
from .base import LLMAnswer, MentorBackend

logger = logging.getLogger(__name__)


class OpenClawBackend(MentorBackend):
    """HTTP-webhook backend routing AskMentor to a live agent session.

    Args:
        url: HTTPS endpoint that implements the wire contract above.
        auth_bearer_env: Name of the env var holding the Bearer token.
            Read lazily on each call so token rotation doesn't
            require restarting the server. Pass ``None`` to omit the
            Authorization header.
        timeout_seconds: Per-call HTTP timeout.
        max_concurrent: Per-backend concurrency cap (Semaphore).
    """

    mode_label = "openclaw-routed"

    def __init__(
        self,
        *,
        url: str,
        auth_bearer_env: str | None = None,
        timeout_seconds: float = 60.0,
        max_concurrent: int = 10,
    ) -> None:
        super().__init__(max_concurrent=max_concurrent)
        self._url = url
        # Read the bearer token from the environment lazily, so a redeploy
        # picking up a fresh token doesn't require restarting the server.
        self._auth_bearer_env = auth_bearer_env
        self._timeout = timeout_seconds

    @property
    def is_live(self) -> bool:
        """``True`` iff a non-empty webhook URL is configured."""
        return bool(self._url)

    def _bearer(self) -> str | None:
        """Read the Bearer token from the configured env var (if any)."""
        if not self._auth_bearer_env:
            return None
        return os.environ.get(self._auth_bearer_env) or None

    async def ask(
        self,
        *,
        mentor_name: str,
        persona: str,
        question: str,
        playbook_bodies: list[tuple[str, str]],
    ) -> LLMAnswer:
        """POST the question to the configured webhook and parse the response.

        Args:
            mentor_name: Display name passed in the ``name`` field.
            persona: Voice/stance string for the mentor.
            question: The mentee's question.
            playbook_bodies: Top-N retrieved ``(title, body)`` pairs.

        Returns:
            ``LLMAnswer`` parsed from the webhook's JSON response.

        Raises:
            RuntimeError: If the webhook returns a non-200 status; the
                server catches and surfaces ``llm_failed`` to the mentee.
        """
        headers = {"Content-Type": "application/json", "User-Agent": f"ammp-mcp/{__version__}"}
        bearer = self._bearer()
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        payload = {
            "name": mentor_name,
            "persona": persona,
            "question": question,
            "playbooks": [{"title": t, "body": b} for t, b in playbook_bodies],
            "ammp_version": __version__,
            "ammp_draft": __ammp_draft__,
        }

        async with self._sem, httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=payload, headers=headers)

        if resp.status_code != 200:
            logger.warning(
                "OpenClaw backend returned %s from %s: %s",
                resp.status_code,
                self._url,
                resp.text[:300],
            )
            raise RuntimeError(f"openclaw_backend_http_{resp.status_code}")

        # The remote service is expected to return the same envelope the
        # Anthropic backend produces. We re-use the parser so a remote
        # that wraps its response in ```json ... ``` fences still works.
        body = resp.text
        return parse_envelope(body)
