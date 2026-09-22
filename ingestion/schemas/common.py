"""Shared technical-validation helpers.

These models validate only what ingestion needs to safely land a row:
a usable source identifier, and that mandatory timestamps are at least
parseable. They deliberately do NOT enforce business semantics (valid
country codes, known plan names, canonical currency casing, ...) - that
normalization is dbt staging's job in Phase 3. A row like
plan_name=None or country_code="USA" is still technically ingestible and
must not be rejected here.
"""
from __future__ import annotations

from datetime import datetime

from dateutil import parser as date_parser


def parse_timestamp(value: str | None) -> datetime | None:
    """Best-effort timestamp parse used only to decide ingestibility.

    Returns None for blank/missing values (which is fine for optional
    timestamp fields). Raises ValueError for a non-blank value that cannot
    be parsed at all, which the loader treats as a technical rejection
    reason for fields where a valid timestamp is mandatory.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return date_parser.parse(text)


def non_blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
