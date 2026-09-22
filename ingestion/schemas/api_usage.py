from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class ApiUsageTechnical(BaseModel):
    request_id: str | None = None
    request_timestamp: str | None = None

    @field_validator("request_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: request_id")
        return v

    @field_validator("request_timestamp")
    @classmethod
    def _timestamp_present_and_parseable(cls, v: str | None) -> str:
        text = non_blank(v)
        if not text:
            raise ValueError("missing required timestamp: request_timestamp")
        parse_timestamp(text)
        return v
