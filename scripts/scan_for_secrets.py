"""Deterministic, offline secret scan over the full git history's added
lines - no third-party action/service, no network access.

Used by .github/workflows/security.yml. Can also be run locally:
    python scripts/scan_for_secrets.py [--ref <git-ref>]

Checks every commit reachable from `--ref` (default: HEAD) for lines that
look like a committed credential: AWS access keys, PEM private key
headers, a handful of common cloud-provider API key prefixes, and a
`postgresql://` URL whose password isn't this project's documented
"change_me_locally"-style placeholder. This is intentionally a small,
explicit pattern list, not a general-purpose entropy-based scanner - it
trades recall for being simple to read, fast, and dependency-free.

Exit code 0: no findings. Exit code 1: at least one finding - printed as
(commit, file, line number) ONLY. The matched line's actual content is
never printed, so a real secret never ends up duplicated into the
(public) CI log.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("PEM private key header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Anthropic-style API key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")),
    ("GitHub personal access token", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
]

# A postgresql:// URL with a real-looking (non-placeholder) password - the
# one credential shape this project's own config files legitimately
# contain examples of, so it needs a placeholder allowlist rather than a
# blanket "any DSN is a finding" rule.
_DSN_RE = re.compile(r"postgresql(?:\+\w+)?://[^:\s]+:([^@\s]+)@")

# This project's own long-standing local-dev placeholder passwords, plus
# the obviously-fake values its test suite deliberately uses for
# unreachable/broken-connection test fixtures (e.g. "nopass",
# "secret" - literal marker strings, not real credentials). This is a
# deliberately permissive allowlist, not an entropy check: it trades
# missing a real secret that happens to collide with one of these exact
# words for not drowning every CI run in false positives from the
# project's own tests. See docs/ci_cd_and_testing.md "Secret scanning
# limitations."
_ALLOWED_DSN_PASSWORDS = {
    "change_me_locally", "change_me_locally_ro", "ci_password", "ci_ro_password",
    "analytics", "nopass", "nopass_secret_value", "pass", "secret", "password",
    "baduser", "badpassword123", "x", "user", "test", "changeme", "admin",
}


def _iter_added_lines(ref: str) -> list[tuple[str, str, int, str]]:
    """Yields (commit_sha, file_path, line_number, line_text) for every
    ADDED line (diff '+' lines, excluding the '+++' file header) across
    every commit reachable from `ref`."""
    log = subprocess.run(
        ["git", "log", "--format=%H", ref],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    results: list[tuple[str, str, int, str]] = []
    for sha in log:
        diff = subprocess.run(
            ["git", "show", "--unified=0", "--format=", sha],
            capture_output=True, text=True, check=True,
        ).stdout
        current_file = "?"
        new_line_no = 0
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                new_line_no = int(match.group(1)) - 1 if match else 0
                continue
            if line.startswith("+") and not line.startswith("+++"):
                new_line_no += 1
                results.append((sha, current_file, new_line_no, line[1:]))
    return results


def scan(ref: str) -> list[str]:
    findings: list[str] = []
    for sha, path, line_no, text in _iter_added_lines(ref):
        for label, pattern in PATTERNS:
            if pattern.search(text):
                findings.append(f"{sha[:12]} {path}:{line_no} - possible {label}")
        dsn_match = _DSN_RE.search(text)
        if dsn_match and dsn_match.group(1) not in _ALLOWED_DSN_PASSWORDS:
            findings.append(f"{sha[:12]} {path}:{line_no} - postgresql:// URL with a non-placeholder password")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()

    findings = scan(args.ref)
    if findings:
        print(f"Found {len(findings)} possible committed secret(s):", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        print(
            "\nIf any of these is a genuine credential: rotate it immediately at the "
            "provider, then remove it from history (it is not enough to delete it in a "
            "new commit - see docs/ci_cd_and_testing.md 'If a secret is found').",
            file=sys.stderr,
        )
        return 1

    print("No likely committed secrets found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
