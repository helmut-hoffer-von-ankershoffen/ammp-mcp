"""LLM-backed AskMentor execution.

Bounded-concurrency executor. Each AskMentor call opens a Claude API
request; concurrency is capped by an asyncio Semaphore (cap is
`settings.llm_max_concurrent`, default 10). Excess requests queue at the
asyncio level — FIFO, fast drain — so the practical worst case under burst
is `LLM-latency × ceil(burst / cap)`. No external queue (Redis etc.) for
v0.2; if/when load grows, add one.

Each call asks the model to emit a small JSON envelope including a
self-reported `confidence ∈ [0,1]`. When confidence falls below the
mentor's threshold, the caller (server.py) sets escalation_recommended on
the response — turning low confidence into a *mentor-triggered*
EscalateToHuman recommendation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMAnswer:
    answer: str
    confidence: float


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


class LLMClient:
    """Async, bounded-concurrency wrapper around the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_concurrent: int,
        timeout_seconds: float,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._sem = asyncio.Semaphore(max_concurrent)
        self._client: AsyncAnthropic | None = None
        if api_key:
            self._client = AsyncAnthropic(api_key=api_key, timeout=timeout_seconds)

    @property
    def is_live(self) -> bool:
        return self._client is not None

    async def ask(
        self,
        *,
        mentor_name: str,
        persona: str,
        question: str,
        playbook_bodies: list[tuple[str, str]],
    ) -> LLMAnswer:
        """Ask the mentor LLM. `playbook_bodies` is [(title, body), ...].
        Returns LLMAnswer; raises on transport failure."""
        if not self._client:
            # No API key wired — return a deterministic stub the server can
            # still surface to the mentee, with low confidence so the
            # confidence-driven escalation kicks in.
            return LLMAnswer(
                answer=(
                    "AskMentor is configured without an Anthropic API key, "
                    "so I cannot synthesise a tailored answer right now. The "
                    "relevant playbooks for this question are listed in the "
                    "response; read them in order."
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
        return _parse_envelope(raw)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_envelope(raw: str) -> LLMAnswer:
    """Extract the {answer, confidence} JSON envelope from the model's
    response. Tolerant: strips fenced code blocks, retries with the
    largest balanced JSON object if the whole string isn't parseable."""
    s = raw.strip()
    # Strip ```json ... ``` fencing if present.
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
