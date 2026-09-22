from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class OrganizationTechnical(BaseModel):
    organization_id: str | None = None
    created_at: str | None = None

    @field_validator("organization_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: organization_id")
        return v

    @field_validator("created_at")
    @classmethod
    def _created_at_parseable(cls, v: str | None) -> str | None:
        text = non_blank(v)
        if not text:
            return None
        parse_timestamp(text)  # raises ValueError if unparseable
        return v
