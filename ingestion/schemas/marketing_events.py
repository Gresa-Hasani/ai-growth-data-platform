from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class MarketingEventTechnical(BaseModel):
    marketing_event_id: str | None = None
    event_timestamp: str | None = None

    @field_validator("marketing_event_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: marketing_event_id")
        return v

    @field_validator("event_timestamp")
    @classmethod
    def _timestamp_present_and_parseable(cls, v: str | None) -> str:
        text = non_blank(v)
        if not text:
            raise ValueError("missing required timestamp: event_timestamp")
        parse_timestamp(text)
        return v
