"""Lightweight, repeatable latency benchmark for the Analytics API and AI
Agent - Phase 12.

This is a measurement tool, not a test: it reports timings, it does not
assert thresholds (a fragile fixed latency threshold in CI would fail for
reasons unrelated to a real regression - slower CI runners, Docker cold
starts, etc.). Use it to compare before/after a change, or to spot-check
the running stack, not as a pass/fail gate.

Usage:
    python scripts/benchmark.py                          # default: localhost:8000, 20 reps
    python scripts/benchmark.py --base-url http://localhost:8000 --reps 50
    python scripts/benchmark.py --skip-agent              # endpoints only, skip /agent/ask

Requires the API to already be running (`docker compose up -d` or
`make api`) and reachable at --base-url. Does not start/stop anything
itself, and never writes to the database - every request it makes is a
GET against a read-only endpoint or a POST to the AI Agent's /ask (which
is itself read-only - see docs/security_and_sql_validation.md).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class Timing:
    label: str
    samples_ms: list[float]

    @property
    def median(self) -> float:
        return statistics.median(self.samples_ms)

    @property
    def p95(self) -> float:
        if len(self.samples_ms) < 2:
            return self.samples_ms[0]
        return statistics.quantiles(self.samples_ms, n=20)[18]  # ~95th percentile

    @property
    def min(self) -> float:
        return min(self.samples_ms)

    @property
    def max(self) -> float:
        return max(self.samples_ms)


def _timed_request(method: str, url: str, body: bytes | None = None) -> tuple[float, int]:
    """Returns (elapsed_ms, http_status). Raises on a connection failure -
    the caller decides whether that's fatal (e.g. the API isn't running)."""
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        # A 4xx/422/503 is still a real, timeable response from the
        # application - only a connection failure (below) means "API down."
        exc.read()
        status = exc.code
    elapsed_ms = (time.perf_counter() - start) * 1000
    return elapsed_ms, status


def benchmark(label: str, method: str, url: str, reps: int, body: bytes | None = None) -> Timing:
    samples: list[float] = []
    for _ in range(reps):
        elapsed_ms, _status = _timed_request(method, url, body)
        samples.append(elapsed_ms)
    return Timing(label=label, samples_ms=samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--reps", type=int, default=20, help="Samples per endpoint (default: 20)")
    parser.add_argument("--skip-agent", action="store_true", help="Skip POST /api/v1/agent/ask (slower, provider-dependent)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a table")
    args = parser.parse_args()

    try:
        _timed_request("GET", f"{args.base_url}/health")
    except (urllib.error.URLError, ConnectionError) as exc:
        print(f"Could not reach {args.base_url} - is the API running? ({type(exc).__name__}: {exc})")
        return 2

    targets: list[tuple[str, str, str, bytes | None]] = [
        ("GET /health (liveness)", "GET", "/health", None),
        ("GET /ready (readiness)", "GET", "/ready", None),
        ("GET /api/v1/analytics/revenue/mrr", "GET", "/api/v1/analytics/revenue/mrr", None),
        ("GET /api/v1/analytics/growth/daily", "GET", "/api/v1/analytics/growth/daily", None),
        ("GET /api/v1/customers?limit=50", "GET", "/api/v1/customers?limit=50", None),
        ("GET /api/v1/quality/latest", "GET", "/api/v1/quality/latest", None),
        ("GET /api/v1/agent/health (bounded provider check)", "GET", "/api/v1/agent/health", None),
    ]
    if not args.skip_agent:
        targets.append((
            "POST /api/v1/agent/ask (deterministic question)",
            "POST", "/api/v1/agent/ask",
            json.dumps({"question": "What is our current MRR?"}).encode(),
        ))

    results: list[Timing] = []
    for label, method, path, body in targets:
        results.append(benchmark(label, method, f"{args.base_url}{path}", args.reps, body))

    if args.json:
        print(json.dumps(
            {
                "base_url": args.base_url, "reps": args.reps,
                "results": [
                    {"label": t.label, "median_ms": round(t.median, 2), "p95_ms": round(t.p95, 2),
                     "min_ms": round(t.min, 2), "max_ms": round(t.max, 2)}
                    for t in results
                ],
            },
            indent=2,
        ))
        return 0

    print(f"Benchmark against {args.base_url} ({args.reps} reps per endpoint)\n")
    print(f"{'Endpoint':<52} {'median':>8} {'p95':>8} {'min':>8} {'max':>8}")
    for t in results:
        print(f"{t.label:<52} {t.median:>6.1f}ms {t.p95:>6.1f}ms {t.min:>6.1f}ms {t.max:>6.1f}ms")

    print(
        "\nNote: this reports timings for comparison, it does not pass/fail. "
        "The /agent/ask timing reflects whatever provider path is actually "
        "taken (real inference vs. the deterministic fallback) - check the "
        "response's meta.used_real_inference to know which one you measured."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
