from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from ingestion.loaders.base import BaseLoader


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


class ParquetLoader(BaseLoader):
    """Reads a Parquet file in row-group-aligned batches via pyarrow.

    All columns are cast to strings on read so raw values (including
    already-injected quality issues like mixed casing) land in the raw
    TEXT columns unchanged, matching the CSV/JSONL loaders' behavior.
    """

    def iter_batches(self, path: Path) -> Iterator[list[dict[str, Any]]]:
        parquet_file = pq.ParquetFile(path)
        for batch in parquet_file.iter_batches(batch_size=self.batch_size):
            table = batch.to_pandas()
            records = table.to_dict(orient="records")
            for record in records:
                for key, value in record.items():
                    record[key] = None if _is_missing(value) else str(value)
            yield records
