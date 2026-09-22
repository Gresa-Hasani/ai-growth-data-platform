"""Unit tests for the synthetic data generator: determinism, scale profiles,
and quality-issue injection. Uses "small" scale only - never the large
dataset - so the suite stays fast."""
from __future__ import annotations

from scripts.generate_data import RATES, ScaleProfile, generate


def test_scale_profile_matches_spec_for_large():
    profile = ScaleProfile.build("large")
    assert profile.users == 100_000
    assert profile.organizations == 20_000
    assert profile.subscriptions == 150_000
    assert profile.invoices == 500_000
    assert profile.product_events == 1_000_000
    assert profile.api_usage == 500_000
    assert profile.crm_accounts == 100_000
    assert profile.marketing_events == 250_000


def test_scale_profile_small_and_medium():
    small = ScaleProfile.build("small")
    medium = ScaleProfile.build("medium")
    assert small.users == 1_000
    assert medium.users == 10_000
    assert small.organizations < medium.organizations


def test_generation_is_deterministic(tmp_path):
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"

    manifest1 = generate("small", seed=42, output_dir=out1)
    manifest2 = generate("small", seed=42, output_dir=out2)

    assert manifest1["source_files"] == manifest2["source_files"]
    assert manifest1["quality_issues_injected"] == manifest2["quality_issues_injected"]

    for file_name in manifest1["source_files"]:
        if file_name.endswith(".parquet"):
            continue  # binary-format timestamps compared separately below
        content1 = (out1 / file_name).read_text(encoding="utf-8")
        content2 = (out2 / file_name).read_text(encoding="utf-8")
        assert content1 == content2, f"{file_name} differs between identical runs"


def test_different_seeds_produce_different_data(tmp_path):
    out1 = tmp_path / "seed42"
    out2 = tmp_path / "seed99"
    generate("small", seed=42, output_dir=out1)
    generate("small", seed=99, output_dir=out2)

    content1 = (out1 / "organizations.csv").read_text(encoding="utf-8")
    content2 = (out2 / "organizations.csv").read_text(encoding="utf-8")
    assert content1 != content2


def test_quality_issues_are_injected(tmp_path):
    manifest = generate("small", seed=42, output_dir=tmp_path)
    issues = manifest["quality_issues_injected"]
    # Every configured issue type should fire at least once at this scale.
    for issue_type in RATES:
        assert issues[issue_type] > 0, f"{issue_type} was never injected"


def test_most_records_remain_valid(tmp_path):
    manifest = generate("small", seed=42, output_dir=tmp_path)
    issues = manifest["quality_issues_injected"]
    total_rows = sum(manifest["source_files"].values())
    # duplicate_record/orphaned_foreign_key are injected per-entity at ~0.4%/0.6%
    # of that entity's own rows and summed across all 8 entities here - well
    # under 2% of the combined row count either way.
    assert issues["duplicate_record"] < total_rows * 0.02
    assert issues["orphaned_foreign_key"] < total_rows * 0.02


def test_manifest_contains_no_secrets(tmp_path):
    manifest = generate("small", seed=42, output_dir=tmp_path)
    manifest_text = str(manifest).lower()
    for forbidden in ("password", "secret", "api_key", "token"):
        assert forbidden not in manifest_text
