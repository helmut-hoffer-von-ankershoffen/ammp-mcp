"""Abstract base class + shared types for mentor backends.

Every concrete backend implements one async method: ``ask(...)``. The
contract is intentionally small so that other implementations
(e.g. a Hermes-on-someone-else's-runtime, or a second Claude Cowork
mentor) can be plugged in without touching the server.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMAnswer:
    """The shape every backend returns.

    ``answer`` is the prose. ``confidence`` is the backend's self-reported
    confidence in [0.0, 1.0]; the server compares it against the mentor's
    threshold and, when below, sets ``escalation_recommended=True`` on the
    AskMentor response (mentor-triggered escalation, AMMP §3.4).
    """

    answer: str
    confidence: float


class MentorBackend(abc.ABC):
    """The pluggable answer engine behind a mentor.

    Subclasses MUST implement :meth:`ask`. They MAY override
    :attr:`mode_label` to control how the capability advertisement
    describes them in ``/.well-known/agent.json``.

    Backends are responsible for their own concurrency control —
    typically an ``asyncio.Semaphore`` — so the server can hand off
    burst load without thinking about per-backend rate limits.

    Args:
        max_concurrent: Maximum number of concurrent ``ask`` calls.
            Excess callers queue at the asyncio level — FIFO, fast
            drain. Tune up if your upstream rate-limit budget is
            higher; down if you want a tighter cost ceiling.
    """

    #: Short, machine-friendly label used in capability advertisement
    #: (``ammp.mentoring.askMentorMode``). Override per backend.
    mode_label: str = "abstract"

    def __init__(self, *, max_concurrent: int = 10) -> None:
        self._sem = asyncio.Semaphore(max_concurrent)

    @abc.abstractmethod
    async def ask(
        self,
        *,
        mentor_name: str,
        persona: str,
        question: str,
        playbook_bodies: list[tuple[str, str]],
    ) -> LLMAnswer:
        """Answer a question on behalf of the mentor.

        Args:
            mentor_name: Display name (e.g. "Pepe Arturo").
            persona: System-prompt-shaped voice/stance string.
            question: The mentee's question, already trimmed.
            playbook_bodies: ``[(title, body), ...]`` — the most relevant
                playbooks the server retrieved for this question.

        Returns:
            ``LLMAnswer(answer, confidence)``. Implementations should
            never raise on a *content* problem — return a low-confidence
            answer and let the server do mentor-triggered escalation.
            Transport errors (network down, auth rejected) MAY raise;
            the server catches and surfaces a structured ``llm_failed``
            error in-band.
        """

    @property
    def is_live(self) -> bool:
        """Whether the backend is actually connected to its external dependency.

        ``False`` for a stub or for a backend that's been configured but
        is missing its credentials. The server uses this to advertise
        accurate capability metadata.
        """
        return True
