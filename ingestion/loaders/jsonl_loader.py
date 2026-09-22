from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ingestion.loaders.base import BaseLoader


class JSONLLoader(BaseLoader):
    """Streams a JSON-Lines file batch by batch.

    A line that fails to parse as JSON is recorded as a parse error (routed
    to rejected_records) instead of crashing the whole file's ingestion.
    """

    def iter_batches(self, path: Path) -> Iterator[list[dict[str, Any]]]:
        batch: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line_number, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    self.parse_errors.append(
                        {
                            "identifier": None,
                            "reason": f"malformed JSON at line {line_number}: {exc}",
                            "payload": {"raw_line": line[:500]},
                        }
                    )
                    continue

                if not isinstance(row, dict):
                    self.parse_errors.append(
                        {
                            "identifier": None,
                            "reason": f"line {line_number} is not a JSON object",
                            "payload": {"raw_line": line[:500]},
                        }
                    )
                    continue

                batch.append(row)
                if len(batch) >= self.batch_size:
                    yield batch
                    batch = []

        if batch or self.parse_errors:
            yield batch
