from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class UserTechnical(BaseModel):
    user_id: str | None = None
    signup_at: str | None = None

    @field_validator("user_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: user_id")
        return v

    @field_validator("signup_at")
    @classmethod
    def _signup_at_parseable(cls, v: str | None) -> str | None:
        text = non_blank(v)
        if not text:
            return None
        parse_timestamp(text)
        return v
