from __future__ import annotations

from pydantic import BaseModel, field_validator

from .common import non_blank, parse_timestamp


class InvoiceTechnical(BaseModel):
    invoice_id: str | None = None
    issued_at: str | None = None

    @field_validator("invoice_id")
    @classmethod
    def _id_present(cls, v: str | None) -> str:
        v = non_blank(v)
        if not v:
            raise ValueError("missing required technical identifier: invoice_id")
        return v

    @field_validator("issued_at")
    @classmethod
    def _issued_at_parseable(cls, v: str | None) -> str | None:
        text = non_blank(v)
        if not text:
            return None
        parse_timestamp(text)
        return v
