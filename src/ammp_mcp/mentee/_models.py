"""Mentee pydantic model."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Mentee(BaseModel):
    """A registered mentee allowed to call the server.

    Identifier is the slug; ``api_key_hash`` is checked when
    ``require_auth`` is ``True``. The api_key (plaintext) is never
    stored — the server hashes incoming keys at request time and
    compares hashes.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    operator: str = Field(description="Who runs this mentee, e.g. 'human:helmut' or 'human:sandra'.")
    runtime: str = Field(description="What the mentee runs on, e.g. 'claude-cowork', 'claude-ai', 'claude-code'.")
    api_key_hash: str = Field(description="Hex sha256 of the API key. Plaintext key is never stored.")
    rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)
