from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class ProductEventTechnical(BaseModel):
    """Events have no technical identity without an id and a timestamp,
    so both are mandatory here (unlike most other sources, where only the
    id is required)."""

    event_id: str | None = None
    event_timestamp: str | None = None

    @field_validator("event_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: event_id")
        return v

    @field_validator("event_timestamp")
    @classmethod
    def _timestamp_present_and_parseable(cls, v: str | None) -> str:
        text = non_blank(v)
        if not text:
            raise ValueError("missing required timestamp: event_timestamp")
        parse_timestamp(text)
        return v
