from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class SubscriptionTechnical(BaseModel):
    subscription_id: str | None = None
    started_at: str | None = None

    @field_validator("subscription_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: subscription_id")
        return v

    @field_validator("started_at")
    @classmethod
    def _started_at_parseable(cls, v: str | None) -> str | None:
        text = non_blank(v)
        if not text:
            return None
        parse_timestamp(text)
        return v
