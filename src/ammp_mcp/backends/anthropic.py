"""Anthropic Messages API backend.

Stateless — the mentor's persona and the relevant playbooks are pasted
into the system prompt on every call; no conversation memory persists
between calls. Cheap, predictable, good for personas whose voice is the
load-bearing thing (and whose "knowledge" is fully captured by their
playbook corpus).

For mentors whose lived state matters (e.g. Pepe Arturo, who has a vault
of memory and ongoing context), use :class:`OpenClawBackend` instead.
"""

from __future__ import annotations

import json
import logging
import re

from anthropic import AsyncAnthropic

from .base import LLMAnswer, MentorBackend

logger = logging.getLogger(__name__)


_SYSTEM_TEMPLATE = """You are {mentor_name}, an agentic mentor responding over AMMP's Mentoring track to a junior agent's question.

Persona and stance:
{persona}

Constraints (load-bearing, non-negotiable):
- Answer in 3–6 sentences. Calm, operator register. No emojis.
- Use ONLY information from the playbooks below and your general knowledge of agent operations. Do not invent specifics.
- If the question is outside what the corpus covers or you are genuinely uncertain, say so plainly. Better to escalate honestly than to guess.
- You are talking to another AI agent, not its human operator. You CANNOT reach the operator. If escalation is needed, the human contact is the mentee's own operator and the mentee handles that escalation.

Your output MUST be a JSON object with exactly two keys, no prose before or after the JSON:
{{
  "answer": "<3-6 sentences>",
  "confidence": <float 0.0-1.0; how confident you are this answer is correct AND covers the question>
}}

Confidence calibration:
- 1.0 = the corpus directly answers this and you are certain
- 0.7 = the corpus covers this topic, your answer is right but partial
- 0.5 = the corpus is adjacent, you reasoned to an answer but you might be wrong
- 0.3 = you don't have grounded coverage; this likely needs the operator
- 0.0 = the question is outside this mentor's domain entirely

Playbooks available (each appended verbatim below):
"""


class AnthropicBackend(MentorBackend):
    """Direct call to the Anthropic Messages API.

    Args:
        api_key: Anthropic API key. ``None`` puts the backend in a
            no-network fallback mode that mirrors StubBackend.
        model: Anthropic model id (e.g. ``"claude-opus-4-7"``).
        max_concurrent: Per-backend concurrency cap (asyncio Semaphore).
        timeout_seconds: Per-call HTTP timeout passed to the SDK.
    """

    mode_label = "anthropic-direct"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_concurrent: int,
        timeout_seconds: float,
    ) -> None:
        super().__init__(max_concurrent=max_concurrent)
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._client: AsyncAnthropic | None = None
        if api_key:
            self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)

    @property
    def is_live(self) -> bool:
        """``True`` iff the backend has an API key and can hit Anthropic."""
        return self._client is not None

    async def ask(
        self,
        *,
        mentor_name: str,
        persona: str,
        question: str,
        playbook_bodies: list[tuple[str, str]],
    ) -> LLMAnswer:
        """Synthesise a mentor answer via the Anthropic Messages API.

        See :meth:`ammp_mcp.backends.base.MentorBackend.ask` for the
        general contract — this implementation pastes ``persona`` and
        the playbook bodies into a system prompt and asks the model
        for a JSON envelope.

        Args:
            mentor_name: Display name (e.g. ``"Pepe Arturo"``).
            persona: Voice/stance string for the mentor.
            question: The mentee's question, already trimmed.
            playbook_bodies: Top-N retrieved ``(title, body)`` pairs.

        Returns:
            ``LLMAnswer`` with the model's prose + self-reported
            confidence in ``[0, 1]``. When no API key is configured,
            returns a deterministic low-confidence placeholder so the
            response shape stays consistent.
        """
        if not self._client:
            # Mirror the StubBackend behaviour so a missing API key
            # doesn't break the response shape — the low confidence
            # triggers mentor-triggered escalation upstream.
            return LLMAnswer(
                answer=(
                    f"AskMentor for {mentor_name} is configured without an Anthropic API key, "
                    "so I cannot synthesise a tailored answer right now. The relevant playbooks "
                    "for this question are listed in the response; read them in order."
                ),
                confidence=0.2,
            )

        system_prompt = _SYSTEM_TEMPLATE.format(mentor_name=mentor_name, persona=persona)
        for title, body in playbook_bodies:
            system_prompt += f"\n────────────\n{title}\n────────────\n{body}\n"

        async with self._sem:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=600,
                system=system_prompt,
                messages=[{"role": "user", "content": question}],
            )

        text_blocks = [b.text for b in resp.content if b.type == "text"]
        raw = "\n".join(text_blocks).strip()
        return parse_envelope(raw)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_envelope(raw: str) -> LLMAnswer:
    """Extract the ``{answer, confidence}`` JSON envelope from a model response.

    Tolerant: strips fenced code blocks, retries with the largest
    balanced JSON object if the whole string isn't parseable, clamps
    confidence to [0, 1]. Used by both AnthropicBackend (parsing model
    output) and OpenClawBackend (parsing the upstream service's response).
    """
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = _JSON_OBJECT_RE.search(s)
        if not m:
            return LLMAnswer(answer=raw[:600], confidence=0.3)
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return LLMAnswer(answer=raw[:600], confidence=0.3)

    answer = str(data.get("answer", raw[:600])).strip()
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))
    return LLMAnswer(answer=answer, confidence=confidence)
