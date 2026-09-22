"""Unit tests for the CSV/JSONL/Parquet loaders and technical validation.

These use tiny fixture files in tests/fixtures/mini/, never the large
generated dataset, and never touch a real database - loader.iter_batches()
and the pure validate/deduplicate helpers are tested in isolation.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ingestion.loaders.csv_loader import CSVLoader
from ingestion.loaders.jsonl_loader import JSONLLoader
from ingestion.loaders.parquet_loader import ParquetLoader
from ingestion.registry import SOURCES

FIXTURES = Path(__file__).parent.parent / "fixtures" / "mini"


def test_csv_loader_reads_all_rows_in_batches():
    spec = SOURCES["organizations"]
    loader = CSVLoader(spec, batch_size=2)
    batches = list(loader.iter_batches(FIXTURES / "organizations.csv"))
    total_rows = sum(len(b) for b in batches)
    assert total_rows == 5  # matches the fixture's row count
    assert all(len(b) <= 2 for b in batches)


def test_csv_loader_preserves_messy_values_as_is():
    spec = SOURCES["organizations"]
    loader = CSVLoader(spec, batch_size=10)
    rows = next(loader.iter_batches(FIXTURES / "organizations.csv"))
    org3 = next(r for r in rows if r["organization_id"] == "org_000003")
    # Missing optional fields land as None, not dropped or coerced.
    assert org3["organization_name"] is None
    assert org3["country_code"] is None


def test_technical_validation_rejects_missing_identifier():
    spec = SOURCES["organizations"]
    loader = CSVLoader(spec, batch_size=10)
    rows = next(loader.iter_batches(FIXTURES / "organizations.csv"))
    valid, rejects = loader._validate(rows)
    assert len(rejects) == 1
    assert "organization_id" in rejects[0]["reason"]
    assert len(valid) == 4


def test_deduplicate_keeps_latest_updated_at():
    spec = SOURCES["organizations"]
    loader = CSVLoader(spec, batch_size=10)
    rows = next(loader.iter_batches(FIXTURES / "organizations.csv"))
    valid, _ = loader._validate(rows)
    deduped, dedup_rejects = loader._deduplicate(valid)

    assert len(dedup_rejects) == 1
    assert dedup_rejects[0]["reason"] == "duplicate business key within source file"

    winner = next(r for r in deduped if r["organization_id"] == "org_000001")
    assert winner["updated_at"] == "2025-03-01T00:00:00Z"  # the later of the two duplicates
    assert len({r["organization_id"] for r in deduped}) == len(deduped)


def test_jsonl_loader_isolates_malformed_lines():
    spec = SOURCES["product_events"]
    loader = JSONLLoader(spec, batch_size=10)
    batches = list(loader.iter_batches(FIXTURES / "product_events.jsonl"))
    rows = [r for b in batches for r in b]

    assert len(rows) == 4  # 5 lines minus 1 unparseable JSON line
    assert len(loader.parse_errors) == 1
    assert "malformed JSON" in loader.parse_errors[0]["reason"]


def test_jsonl_loader_technical_validation_catches_missing_timestamp_and_id():
    spec = SOURCES["product_events"]
    loader = JSONLLoader(spec, batch_size=10)
    rows = next(loader.iter_batches(FIXTURES / "product_events.jsonl"))
    valid, rejects = loader._validate(rows)

    assert len(valid) == 2
    reasons = " ".join(r["reason"] for r in rejects)
    assert "event_timestamp" in reasons
    assert "event_id" in reasons


def test_parquet_loader_roundtrips_batches(tmp_path):
    spec = SOURCES["api_usage"]
    df = pd.DataFrame(
        [
            {
                "request_id": f"req_{i:03d}",
                "user_id": "usr_000001",
                "organization_id": "org_000001",
                "endpoint": "/v1/text-to-speech",
                "model_family": "tts-standard",
                "request_timestamp": "2025-01-01T00:00:00Z",
                "latency_ms": 120,
                "input_units": 100,
                "output_units": 100,
                "status_code": 200,
                "estimated_cost": 0.01,
            }
            for i in range(5)
        ]
    )
    path = tmp_path / "api_usage.parquet"
    df.to_parquet(path, index=False)

    loader = ParquetLoader(spec, batch_size=2)
    rows = [r for b in loader.iter_batches(path) for r in b]

    assert len(rows) == 5
    # Everything lands as strings, matching the raw TEXT columns.
    assert rows[0]["latency_ms"] == "120"
    assert isinstance(rows[0]["request_id"], str)


def test_csv_loader_missing_file_raises():
    spec = SOURCES["organizations"]
    loader = CSVLoader(spec, batch_size=10)
    with pytest.raises(FileNotFoundError):
        list(loader.iter_batches(FIXTURES / "does_not_exist.csv"))
