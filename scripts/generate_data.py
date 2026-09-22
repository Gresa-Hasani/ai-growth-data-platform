"""Deterministic synthetic source-data generator.

Simulates 8 independent operational systems (application DB, billing,
product analytics, API gateway, CRM, marketing) as flat files, the way a
real company's fragmented source estate would hand data to ingestion.

Usage:
    python scripts/generate_data.py --scale small --seed 42
    python scripts/generate_data.py --scale large --seed 42 --output-dir data/generated --clean

Determinism: every random choice is drawn from `random.Random(seed)` /
`numpy.random.default_rng(seed)` instances created once at the top of
`generate()` and threaded through in a fixed call order, so the same
--scale/--seed always produces byte-identical files.
"""
from __future__ import annotations

import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import typer
from faker import Faker

app = typer.Typer(add_completion=False)

NOW = datetime(2026, 9, 22, tzinfo=UTC)
HISTORY_START = NOW - timedelta(days=3 * 365)

# ---------------------------------------------------------------------------
# Scale profiles
# ---------------------------------------------------------------------------

SCALE_USER_COUNTS = {"small": 1_000, "medium": 10_000, "large": 100_000}

# Ratios calibrated so `large` matches the spec's target row counts:
# organizations=20k, subscriptions=150k, invoices=500k, product_events=1M,
# api_usage=500k, crm_accounts=100k, marketing_events=250k (all off 100k users).
RATIO_ORGANIZATIONS = 0.20
RATIO_SUBSCRIPTIONS = 1.50
RATIO_INVOICES = 5.00
RATIO_PRODUCT_EVENTS = 10.00
RATIO_API_USAGE = 5.00
RATIO_CRM_PER_ORG = 5.00
RATIO_MARKETING_EVENTS = 2.50


@dataclass(frozen=True)
class ScaleProfile:
    scale: str
    users: int
    organizations: int
    subscriptions: int
    invoices: int
    product_events: int
    api_usage: int
    crm_accounts: int
    marketing_events: int

    @classmethod
    def build(cls, scale: str) -> ScaleProfile:
        users = SCALE_USER_COUNTS[scale]
        organizations = max(1, round(users * RATIO_ORGANIZATIONS))
        return cls(
            scale=scale,
            users=users,
            organizations=organizations,
            subscriptions=round(users * RATIO_SUBSCRIPTIONS),
            invoices=round(users * RATIO_INVOICES),
            product_events=round(users * RATIO_PRODUCT_EVENTS),
            api_usage=round(users * RATIO_API_USAGE),
            crm_accounts=round(organizations * RATIO_CRM_PER_ORG),
            marketing_events=round(users * RATIO_MARKETING_EVENTS),
        )


# ---------------------------------------------------------------------------
# Reference data / distributions
# ---------------------------------------------------------------------------

ORG_TYPES = ["individual", "startup", "business", "enterprise"]
ORG_TYPE_WEIGHTS = [0.40, 0.30, 0.20, 0.10]

CLEAN_COUNTRY_CODES = ["US", "GB", "DE", "FR", "CA", "AU", "NL", "IN", "BR", "JP", "ES", "IT"]
COUNTRY_WEIGHTS = [0.30, 0.10, 0.08, 0.07, 0.07, 0.06, 0.05, 0.09, 0.06, 0.05, 0.04, 0.03]
# Messy variants injected for a subset of rows (quality issue #7 in the spec).
COUNTRY_DIRTY_VARIANTS = {
    "US": ["USA", "us", "United States", " US"],
    "GB": ["UK", "gb", "United Kingdom"],
}

ACQUISITION_CHANNELS = [
    "organic",
    "paid_search",
    "paid_social",
    "content",
    "referral",
    "partnership",
    "outbound_sales",
]
# Conversion quality varies by channel - referral/partnership convert best,
# paid_social worst - so later trial-conversion analysis has a real signal.
CHANNEL_WEIGHTS = [0.28, 0.20, 0.18, 0.12, 0.10, 0.07, 0.05]
CHANNEL_CONVERSION_LIFT = {
    "organic": 1.0,
    "paid_search": 0.9,
    "paid_social": 0.6,
    "content": 1.1,
    "referral": 1.6,
    "partnership": 1.5,
    "outbound_sales": 1.2,
}

USER_STATUSES = ["active", "churned", "trial"]

PLANS = [
    # (name, monthly_price_usd)
    ("free", 0),
    ("starter", 15),
    ("creator", 39),
    ("pro", 99),
    ("business", 299),
    ("enterprise", 1500),
]
PLAN_NAMES = [p[0] for p in PLANS]
PLAN_PRICE = dict(PLANS)
# Plan mix depends on organization_type - enterprise orgs skew to business/enterprise plans.
PLAN_WEIGHTS_BY_ORG_TYPE = {
    "individual": [0.35, 0.30, 0.20, 0.10, 0.04, 0.01],
    "startup": [0.15, 0.25, 0.25, 0.20, 0.10, 0.05],
    "business": [0.05, 0.10, 0.20, 0.30, 0.25, 0.10],
    "enterprise": [0.02, 0.03, 0.05, 0.15, 0.35, 0.40],
}
BILLING_INTERVALS = ["monthly", "annual"]
SUBSCRIPTION_STATUSES = ["trialing", "active", "canceled", "past_due"]

CURRENCIES = ["USD", "EUR", "GBP"]
CURRENCY_WEIGHTS = [0.70, 0.20, 0.10]
CURRENCY_DIRTY_VARIANTS = {"USD": ["usd", "Usd"], "EUR": ["eur"], "GBP": ["gbp"]}

INVOICE_STATUSES = ["paid", "open", "failed", "void"]
INVOICE_STATUS_WEIGHTS = [0.82, 0.08, 0.07, 0.03]

PRODUCT_EVENT_NAMES = [
    "account_created",
    "project_created",
    "voice_generated",
    "agent_created",
    "agent_conversation_started",
    "api_key_created",
    "audio_generated",
    "feature_used",
]
PRODUCT_EVENT_WEIGHTS = [0.03, 0.07, 0.28, 0.05, 0.12, 0.03, 0.30, 0.12]

API_ENDPOINTS = [
    "/v1/text-to-speech",
    "/v1/voices",
    "/v1/agents/converse",
    "/v1/agents",
    "/v1/audio-isolation",
    "/v1/usage",
]
MODEL_FAMILIES = ["tts-standard", "tts-turbo", "conversational-v1", "conversational-v2"]

CRM_ACCOUNT_STATUSES = ["prospect", "active", "at_risk", "churned"]
CRM_SEGMENTS = ["smb", "mid_market", "enterprise"]
CRM_OWNER_TEAMS = ["growth", "sales", "success", "partnerships"]
CRM_LEAD_SOURCES = ["inbound", "outbound", "partner", "event", "referral"]

MARKETING_CHANNELS = ["google_ads", "meta_ads", "linkedin_ads", "email", "organic_search", "blog"]
MARKETING_EVENT_TYPES = ["impression", "click", "signup_start", "signup_complete"]

# ---------------------------------------------------------------------------
# Quality-issue injection rates (deterministic given the seed; documented in
# docs/ingestion.md). Kept low enough that most records stay valid.
# ---------------------------------------------------------------------------

RATES = {
    "duplicate_record": 0.004,
    "invalid_email": 0.015,
    "null_plan_name": 0.02,
    "messy_country_code": 0.06,
    "messy_currency_casing": 0.06,
    "whitespace_padding": 0.03,
    "mixed_casing": 0.02,
    "missing_optional_value": 0.05,
    "orphaned_foreign_key": 0.006,
    "invalid_status_value": 0.003,
    "missing_campaign_id": 0.15,  # marketing events specifically
}


@dataclass
class QualityIssueCounters:
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in RATES})

    def bump(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def _rand_id(prefix: str, i: int, width: int) -> str:
    return f"{prefix}_{i:0{width}d}"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _maybe_pad_whitespace(rng: random.Random, value: str, counters: QualityIssueCounters) -> str:
    if rng.random() < RATES["whitespace_padding"]:
        counters.bump("whitespace_padding")
        return f"  {value}  "
    return value


def _maybe_mixed_case(rng: random.Random, value: str, counters: QualityIssueCounters) -> str:
    if rng.random() < RATES["mixed_casing"]:
        counters.bump("mixed_casing")
        return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in value)
    return value


def _messy_country(rng: random.Random, code: str, counters: QualityIssueCounters) -> str:
    if code in COUNTRY_DIRTY_VARIANTS and rng.random() < RATES["messy_country_code"]:
        counters.bump("messy_country_code")
        return rng.choice(COUNTRY_DIRTY_VARIANTS[code])
    return code


def _messy_currency(rng: random.Random, code: str, counters: QualityIssueCounters) -> str:
    if code in CURRENCY_DIRTY_VARIANTS and rng.random() < RATES["messy_currency_casing"]:
        counters.bump("messy_currency_casing")
        return rng.choice(CURRENCY_DIRTY_VARIANTS[code])
    return code


def _maybe_missing(rng: random.Random, value: Any, counters: QualityIssueCounters) -> Any:
    if rng.random() < RATES["missing_optional_value"]:
        counters.bump("missing_optional_value")
        return None
    return value


def _maybe_invalid_email(rng: random.Random, email: str, counters: QualityIssueCounters) -> str:
    if rng.random() < RATES["invalid_email"]:
        counters.bump("invalid_email")
        variant = rng.choice(["no_at", "no_domain", "spaces"])
        if variant == "no_at":
            return email.replace("@", "_at_")
        if variant == "no_domain":
            return email.split("@")[0] + "@"
        return f" {email} "
    return email


def _inject_duplicates(
    rng: random.Random, rows: list[dict[str, Any]], counters: QualityIssueCounters
) -> list[dict[str, Any]]:
    """Append clones of randomly chosen existing rows to simulate a source
    system re-delivering the same record (same business key)."""
    n_dupes = int(len(rows) * RATES["duplicate_record"])
    if n_dupes == 0 or not rows:
        return rows
    dupes = [dict(rng.choice(rows)) for _ in range(n_dupes)]
    counters.bump("duplicate_record", n_dupes)
    combined = rows + dupes
    rng.shuffle(combined)
    return combined


def _inject_orphan_fk(
    rng: random.Random, rows: list[dict[str, Any]], fk_field: str, counters: QualityIssueCounters
) -> None:
    n_orphans = int(len(rows) * RATES["orphaned_foreign_key"])
    for row in rng.sample(rows, min(n_orphans, len(rows))):
        if row.get(fk_field):
            row[fk_field] = row[fk_field][:4] + "_ORPHAN_" + row[fk_field][-6:]
            counters.bump("orphaned_foreign_key")


def _inject_invalid_status(
    rng: random.Random, rows: list[dict[str, Any]], status_field: str, counters: QualityIssueCounters
) -> None:
    n_bad = int(len(rows) * RATES["invalid_status_value"])
    for row in rng.sample(rows, min(n_bad, len(rows))):
        row[status_field] = "UNKNOWN_STATUS"
        counters.bump("invalid_status_value")


# ---------------------------------------------------------------------------
# Generators (one per entity, in dependency order)
# ---------------------------------------------------------------------------


def generate_organizations(
    profile: ScaleProfile, rng: random.Random, np_rng: np.random.Generator,
    faker: Faker, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    rows = []
    for i in range(1, profile.organizations + 1):
        org_id = _rand_id("org", i, 6)
        org_type = rng.choices(ORG_TYPES, weights=ORG_TYPE_WEIGHTS, k=1)[0]
        country = rng.choices(CLEAN_COUNTRY_CODES, weights=COUNTRY_WEIGHTS, k=1)[0]
        created_at = HISTORY_START + timedelta(
            seconds=int(np_rng.uniform(0, (NOW - HISTORY_START).total_seconds()))
        )
        updated_at = created_at + timedelta(days=int(np_rng.uniform(0, 60)))
        name = faker.company()
        domain = _maybe_missing(
            rng, name.lower().replace(",", "").replace(" ", "").replace(".", "")[:20] + ".com", counters
        )
        display_name = _maybe_pad_whitespace(rng, _maybe_mixed_case(rng, name, counters), counters)
        rows.append(
            {
                "organization_id": org_id,
                "organization_name": display_name,
                "domain": domain,
                "country_code": _messy_country(rng, country, counters),
                "organization_type": org_type,
                "created_at": _iso(created_at),
                "updated_at": _iso(updated_at),
            }
        )
    _inject_invalid_status(rng, rows, "organization_type", counters)
    rows = _inject_duplicates(rng, rows, counters)
    return rows


def generate_users(
    profile: ScaleProfile, organizations: list[dict[str, Any]], rng: random.Random,
    np_rng: np.random.Generator, faker: Faker, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    rows = []
    org_by_type: dict[str, list[dict[str, Any]]] = {t: [] for t in ORG_TYPES}
    for org in organizations:
        org_by_type.setdefault(org["organization_type"], []).append(org)

    for i in range(1, profile.users + 1):
        user_id = _rand_id("usr", i, 8)
        org = rng.choice(organizations)
        org_created = datetime.strptime(org["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        signup_at = org_created + timedelta(seconds=int(np_rng.uniform(0, (NOW - org_created).total_seconds())))
        channel = rng.choices(ACQUISITION_CHANNELS, weights=CHANNEL_WEIGHTS, k=1)[0]

        # Recent signups skew "trial"; older accounts skew "active"/"churned".
        account_age_days = (NOW - signup_at).days
        if account_age_days < 14:
            status = rng.choices(USER_STATUSES, weights=[0.15, 0.05, 0.80], k=1)[0]
        else:
            churn_base = 0.25 / CHANNEL_CONVERSION_LIFT[channel]
            status = rng.choices(
                USER_STATUSES, weights=[1 - churn_base - 0.02, churn_base, 0.02], k=1
            )[0]

        country = org["country_code"].strip().upper()[:2] if org["country_code"] else rng.choice(CLEAN_COUNTRY_CODES)
        if country not in CLEAN_COUNTRY_CODES:
            country = rng.choices(CLEAN_COUNTRY_CODES, weights=COUNTRY_WEIGHTS, k=1)[0]

        email = _maybe_pad_whitespace(rng, faker.email(), counters)
        updated_at = signup_at + timedelta(days=int(np_rng.uniform(0, max(account_age_days, 1))))

        rows.append(
            {
                "user_id": user_id,
                "organization_id": org["organization_id"],
                "email": _maybe_invalid_email(rng, email, counters),
                "country_code": _messy_country(rng, country, counters),
                "signup_at": _iso(signup_at),
                "acquisition_channel": channel,
                "status": status,
                "created_at": _iso(signup_at),
                "updated_at": _iso(updated_at),
            }
        )

    _inject_orphan_fk(rng, rows, "organization_id", counters)
    rows = _inject_duplicates(rng, rows, counters)
    return rows


def generate_subscriptions(
    profile: ScaleProfile, users: list[dict[str, Any]], organizations: list[dict[str, Any]],
    rng: random.Random, np_rng: np.random.Generator, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    org_by_id = {o["organization_id"]: o for o in organizations}
    rows = []
    for i in range(1, profile.subscriptions + 1):
        sub_id = _rand_id("sub", i, 8)
        user = rng.choice(users)
        org = org_by_id.get(user["organization_id"])
        org_type = org["organization_type"] if org else "individual"

        plan = rng.choices(PLAN_NAMES, weights=PLAN_WEIGHTS_BY_ORG_TYPE[org_type], k=1)[0]
        billing_interval = rng.choices(BILLING_INTERVALS, weights=[0.7, 0.3], k=1)[0]
        currency = rng.choices(CURRENCIES, weights=CURRENCY_WEIGHTS, k=1)[0]

        signup_at = datetime.strptime(user["signup_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        started_at = signup_at + timedelta(hours=int(np_rng.uniform(0, 72)))

        base_price = PLAN_PRICE[plan]
        amount = base_price * (10 if billing_interval == "annual" and base_price else 1)
        amount = round(amount * float(np_rng.uniform(0.95, 1.05)), 2) if amount else 0.0

        trial_started_at: str | None = None
        trial_ended_at: str | None = None
        if plan != "free" and rng.random() < 0.6:
            trial_started_at = _iso(started_at)
            trial_ended_at = _iso(started_at + timedelta(days=14))

        # Users with meaningful engagement (proxy: non-trial user status) convert/stay active more.
        if user["status"] == "churned":
            status = rng.choices(SUBSCRIPTION_STATUSES, weights=[0.05, 0.15, 0.70, 0.10], k=1)[0]
        elif user["status"] == "trial":
            status = rng.choices(SUBSCRIPTION_STATUSES, weights=[0.70, 0.20, 0.05, 0.05], k=1)[0]
        else:
            status = rng.choices(SUBSCRIPTION_STATUSES, weights=[0.05, 0.85, 0.05, 0.05], k=1)[0]

        cancelled_at = None
        if status == "canceled":
            cancelled_at = _iso(started_at + timedelta(days=int(np_rng.uniform(7, 400))))

        current_period_start = started_at
        period_days = 365 if billing_interval == "annual" else 30
        current_period_end = current_period_start + timedelta(days=period_days)
        updated_at = min(NOW, current_period_end)

        plan_name_out: str | None = plan
        if rng.random() < RATES["null_plan_name"]:
            plan_name_out = None
            counters.bump("null_plan_name")

        rows.append(
            {
                "subscription_id": sub_id,
                "user_id": user["user_id"],
                "organization_id": user["organization_id"],
                "plan_name": plan_name_out,
                "billing_interval": billing_interval,
                "status": status,
                "currency": _messy_currency(rng, currency, counters),
                "amount": str(amount),
                "started_at": _iso(started_at),
                "trial_started_at": trial_started_at,
                "trial_ended_at": trial_ended_at,
                "cancelled_at": cancelled_at,
                "current_period_start": _iso(current_period_start),
                "current_period_end": _iso(current_period_end),
                "updated_at": _iso(updated_at),
            }
        )

    _inject_orphan_fk(rng, rows, "user_id", counters)
    _inject_invalid_status(rng, rows, "status", counters)
    rows = _inject_duplicates(rng, rows, counters)
    return rows


def generate_invoices(
    profile: ScaleProfile, subscriptions: list[dict[str, Any]], rng: random.Random,
    np_rng: np.random.Generator, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    rows = []
    paying_subs = [s for s in subscriptions if s["plan_name"] not in (None, "free")]
    pool = paying_subs or subscriptions
    for i in range(1, profile.invoices + 1):
        invoice_id = _rand_id("inv", i, 8)
        sub = rng.choice(pool)
        started_at = datetime.strptime(sub["started_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        issued_at = started_at + timedelta(days=int(np_rng.uniform(0, 300)))
        if issued_at > NOW:
            issued_at = NOW - timedelta(days=int(np_rng.uniform(0, 30)))

        try:
            subtotal = float(sub["amount"])
        except (TypeError, ValueError):
            subtotal = 0.0
        tax = round(subtotal * 0.08, 2)
        total = round(subtotal + tax, 2)

        status = rng.choices(INVOICE_STATUSES, weights=INVOICE_STATUS_WEIGHTS, k=1)[0]
        paid_at = _iso(issued_at + timedelta(days=int(np_rng.uniform(0, 5)))) if status == "paid" else None

        rows.append(
            {
                "invoice_id": invoice_id,
                "subscription_id": sub["subscription_id"],
                "customer_id": sub["organization_id"] or sub["user_id"],
                "currency": _messy_currency(rng, sub["currency"].strip().upper()[:3] if sub["currency"] else "USD", counters),
                "subtotal": str(subtotal),
                "tax": str(tax),
                "total": str(total),
                "status": status,
                "issued_at": _iso(issued_at),
                "paid_at": paid_at,
            }
        )

    _inject_orphan_fk(rng, rows, "subscription_id", counters)
    # Duplicate invoices: exact clone with same invoice_id (classic re-billing-run bug).
    rows = _inject_duplicates(rng, rows, counters)
    return rows


def generate_product_events(
    profile: ScaleProfile, users: list[dict[str, Any]], rng: random.Random,
    np_rng: np.random.Generator, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    rows = []
    active_bias = {"active": 1.6, "trial": 1.2, "churned": 0.3}
    for i in range(1, profile.product_events + 1):
        event_id = _rand_id("evt", i, 9)
        user = rng.choice(users)
        weight = active_bias.get(user["status"], 1.0)
        if rng.random() > min(weight / 1.6, 1.0) and user["status"] == "churned":
            # Bias fewer events toward churned users by occasionally skipping/reassigning.
            user = rng.choice(users)

        signup_at = datetime.strptime(user["signup_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        window_end = NOW if user["status"] != "churned" else min(NOW, signup_at + timedelta(days=180))
        span = max((window_end - signup_at).total_seconds(), 60)
        event_ts = signup_at + timedelta(seconds=int(np_rng.uniform(0, span)))

        event_name = rng.choices(PRODUCT_EVENT_NAMES, weights=PRODUCT_EVENT_WEIGHTS, k=1)[0]
        session_id = f"sess_{rng.randrange(10**8):08d}"
        properties = {"platform": rng.choice(["web", "api", "mobile"]), "value": rng.randint(1, 500)}

        rows.append(
            {
                "event_id": event_id,
                "user_id": user["user_id"],
                "organization_id": user["organization_id"],
                "event_name": event_name,
                "event_timestamp": _iso(event_ts),
                "session_id": session_id,
                "properties": properties,
            }
        )

    _inject_orphan_fk(rng, rows, "user_id", counters)
    rows = _inject_duplicates(rng, rows, counters)
    # Simulate late-arriving / out-of-order events: shuffle a slice of the file
    # rather than keeping it timestamp-sorted (source systems rarely deliver in order).
    shuffle_slice = rows[: max(1, len(rows) // 20)]
    rng.shuffle(shuffle_slice)
    rows[: len(shuffle_slice)] = shuffle_slice
    rng.shuffle(rows)
    return rows


def generate_api_usage(
    profile: ScaleProfile, users: list[dict[str, Any]], organizations: list[dict[str, Any]],
    rng: random.Random, np_rng: np.random.Generator, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    org_by_id = {o["organization_id"]: o for o in organizations}
    rows = []
    for i in range(1, profile.api_usage + 1):
        request_id = _rand_id("req", i, 9)
        user = rng.choice(users)
        org = org_by_id.get(user["organization_id"])
        org_type = org["organization_type"] if org else "individual"
        # Enterprise orgs generate disproportionately more usage per request drawn.
        if org_type == "enterprise" and rng.random() < 0.5:
            user = rng.choice([u for u in users if u["organization_id"] == user["organization_id"]] or [user])

        signup_at = datetime.strptime(user["signup_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        span = max((NOW - signup_at).total_seconds(), 60)
        ts = signup_at + timedelta(seconds=int(np_rng.uniform(0, span)))

        endpoint = rng.choice(API_ENDPOINTS)
        model_family = rng.choice(MODEL_FAMILIES)
        latency_ms = max(20, int(np_rng.normal(220, 80)))
        input_units = int(np_rng.gamma(2.0, 300))
        output_units = int(input_units * np_rng.uniform(0.8, 1.2))
        status_code = rng.choices([200, 200, 200, 429, 500], weights=[80, 10, 5, 3, 2], k=1)[0]
        estimated_cost = round((input_units + output_units) * 0.00003, 6)

        rows.append(
            {
                "request_id": request_id,
                "user_id": user["user_id"],
                "organization_id": user["organization_id"],
                "endpoint": endpoint,
                "model_family": model_family,
                "request_timestamp": _iso(ts),
                "latency_ms": str(latency_ms),
                "input_units": str(input_units),
                "output_units": str(output_units),
                "status_code": str(status_code),
                "estimated_cost": str(estimated_cost),
            }
        )

    _inject_orphan_fk(rng, rows, "user_id", counters)
    rows = _inject_duplicates(rng, rows, counters)
    rng.shuffle(rows)
    return rows


def generate_crm_accounts(
    profile: ScaleProfile, organizations: list[dict[str, Any]], rng: random.Random,
    np_rng: np.random.Generator, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    rows = []
    i = 0
    per_org = max(1, profile.crm_accounts // max(1, len(organizations)))
    for org in organizations:
        touches = rng.randint(max(1, per_org - 2), per_org + 2)
        created_at = datetime.strptime(org["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        segment = "enterprise" if org["organization_type"] == "enterprise" else (
            "mid_market" if org["organization_type"] == "business" else "smb"
        )
        for _ in range(touches):
            i += 1
            if i > profile.crm_accounts:
                break
            crm_id = _rand_id("crm", i, 7)
            updated_at = created_at + timedelta(days=int(np_rng.uniform(0, 400)))
            rows.append(
                {
                    "crm_account_id": crm_id,
                    "organization_id": org["organization_id"],
                    "account_status": rng.choice(CRM_ACCOUNT_STATUSES),
                    "segment": segment,
                    "owner_team": rng.choice(CRM_OWNER_TEAMS),
                    "lead_source": _maybe_missing(rng, rng.choice(CRM_LEAD_SOURCES), counters),
                    "created_at": _iso(created_at),
                    "updated_at": _iso(updated_at),
                }
            )
        if i > profile.crm_accounts:
            break

    _inject_orphan_fk(rng, rows, "organization_id", counters)
    rows = _inject_duplicates(rng, rows, counters)
    return rows


def generate_marketing_events(
    profile: ScaleProfile, users: list[dict[str, Any]], rng: random.Random,
    np_rng: np.random.Generator, counters: QualityIssueCounters,
) -> list[dict[str, Any]]:
    rows = []
    for i in range(1, profile.marketing_events + 1):
        mkt_id = _rand_id("mkt", i, 9)
        anonymous = rng.random() < 0.35
        user = None if anonymous else rng.choice(users)
        anon_id = f"anon_{rng.randrange(10**10):010d}"

        channel = rng.choices(MARKETING_CHANNELS, weights=[0.25, 0.15, 0.10, 0.15, 0.20, 0.15], k=1)[0]
        event_type = rng.choices(
            MARKETING_EVENT_TYPES, weights=[0.55, 0.25, 0.13, 0.07], k=1
        )[0]

        if user is not None:
            base_ts = datetime.strptime(user["signup_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            event_ts = base_ts - timedelta(days=int(np_rng.uniform(0, 14)))
        else:
            event_ts = HISTORY_START + timedelta(
                seconds=int(np_rng.uniform(0, (NOW - HISTORY_START).total_seconds()))
            )

        campaign_id: str | None = f"camp_{rng.randrange(1, 500):04d}"
        if rng.random() < RATES["missing_campaign_id"]:
            campaign_id = None
            counters.bump("missing_campaign_id")

        rows.append(
            {
                "marketing_event_id": mkt_id,
                "user_id": user["user_id"] if user else None,
                "anonymous_id": anon_id,
                "campaign_id": campaign_id,
                "channel": channel,
                "event_type": event_type,
                "event_timestamp": _iso(event_ts),
                "utm_source": channel.split("_")[0],
                "utm_medium": "cpc" if "ads" in channel else channel,
                "utm_campaign": campaign_id or "unknown",
            }
        )

    _inject_orphan_fk(rng, [r for r in rows if r["user_id"]], "user_id", counters)
    rows = _inject_duplicates(rng, rows, counters)
    rng.shuffle(rows)
    return rows


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_jsonl(rows: list[dict[str, Any]], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, default=str) + "\n" for row in rows)


def _write_parquet(rows: list[dict[str, Any]], path: Path) -> None:
    df = pd.DataFrame(rows)
    df.to_parquet(path, index=False)


def generate(scale: str, seed: int, output_dir: Path) -> dict[str, Any]:
    profile = ScaleProfile.build(scale)
    rng = random.Random(seed)
    np_rng = np.random.default_rng(seed)
    faker = Faker()
    faker.seed_instance(seed)
    counters = QualityIssueCounters()

    organizations = generate_organizations(profile, rng, np_rng, faker, counters)
    users = generate_users(profile, organizations, rng, np_rng, faker, counters)
    subscriptions = generate_subscriptions(profile, users, organizations, rng, np_rng, counters)
    invoices = generate_invoices(profile, subscriptions, rng, np_rng, counters)
    product_events = generate_product_events(profile, users, rng, np_rng, counters)
    api_usage = generate_api_usage(profile, users, organizations, rng, np_rng, counters)
    crm_accounts = generate_crm_accounts(profile, organizations, rng, np_rng, counters)
    marketing_events = generate_marketing_events(profile, users, rng, np_rng, counters)

    output_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "organizations.csv": (organizations, _write_csv),
        "users.csv": (users, _write_csv),
        "subscriptions.csv": (subscriptions, _write_csv),
        "invoices.csv": (invoices, _write_csv),
        "product_events.jsonl": (product_events, _write_jsonl),
        "api_usage.parquet": (api_usage, _write_parquet),
        "crm_accounts.csv": (crm_accounts, _write_csv),
        "marketing_events.csv": (marketing_events, _write_csv),
    }

    row_counts = {}
    for file_name, (rows, writer) in files.items():
        writer(rows, output_dir / file_name)
        row_counts[file_name] = len(rows)

    manifest = {
        "generation_timestamp": datetime.now(UTC).isoformat(),
        "seed": seed,
        "scale": scale,
        "scale_profile": {
            "users": profile.users,
            "organizations": profile.organizations,
            "subscriptions": profile.subscriptions,
            "invoices": profile.invoices,
            "product_events": profile.product_events,
            "api_usage": profile.api_usage,
            "crm_accounts": profile.crm_accounts,
            "marketing_events": profile.marketing_events,
        },
        "source_files": row_counts,
        "quality_issues_injected": counters.counts,
        "quality_issue_rates_configured": RATES,
    }
    with open(output_dir / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest


@app.command()
def main(
    scale: str = typer.Option("small", help="small | medium | large"),
    seed: int = typer.Option(42, help="Random seed for deterministic generation."),
    output_dir: str = typer.Option("data/generated", help="Directory to write source files to."),
    clean: bool = typer.Option(False, help="Delete output-dir contents before generating."),
) -> None:
    if scale not in SCALE_USER_COUNTS:
        typer.echo(f"invalid --scale: {scale}. Must be one of {list(SCALE_USER_COUNTS)}", err=True)
        raise typer.Exit(code=2)

    out = Path(output_dir)
    if clean and out.exists():
        shutil.rmtree(out)

    manifest = generate(scale, seed, out)
    typer.echo(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    app()
