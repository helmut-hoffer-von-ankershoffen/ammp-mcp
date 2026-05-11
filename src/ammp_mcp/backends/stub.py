"""Deterministic stub backend.

Returns a fixed low-confidence answer. Used:

- in tests, where we want a synchronous, predictable answer without
  hitting a network;
- in development, where you want to exercise the response shape and
  the mentor-triggered escalation path without configuring an API key.

Confidence is fixed at 0.2 so the server's threshold check (default
0.6) trips and ``escalation_recommended=True`` is returned to the
mentee — exercising the full wiring even offline.
"""

from __future__ import annotations

from .base import LLMAnswer, MentorBackend


class StubBackend(MentorBackend):
    """No-network backend that always returns a low-confidence answer.

    Args:
        max_concurrent: Concurrency cap (unused in practice — the
            stub never blocks — but kept for API parity with the
            other backends).
    """

    mode_label = "stub"

    def __init__(self, *, max_concurrent: int = 10) -> None:
        super().__init__(max_concurrent=max_concurrent)

    @property
    def is_live(self) -> bool:
        """Always ``False`` — the stub never reaches an external system."""
        return False

    async def ask(
        self,
        *,
        mentor_name: str,
        persona: str,  # noqa: ARG002
        question: str,  # noqa: ARG002
        playbook_bodies: list[tuple[str, str]],  # noqa: ARG002
    ) -> LLMAnswer:
        """Return a fixed low-confidence answer naming the mentor.

        Args:
            mentor_name: Display name woven into the canned answer.
            persona: Unused.
            question: Unused.
            playbook_bodies: Unused.

        Returns:
            ``LLMAnswer`` with ``confidence=0.2`` so the server's
            threshold check trips and mentor-triggered escalation
            kicks in for any reasonable threshold.
        """
        return LLMAnswer(
            answer=(
                f"AskMentor for {mentor_name} is configured with the stub backend, "
                "so no tailored synthesis is performed. The relevant playbooks for "
                "this question are listed in the response — read them in order, "
                "and consider escalating to your operator if you remain stuck."
            ),
            confidence=0.2,
        )
