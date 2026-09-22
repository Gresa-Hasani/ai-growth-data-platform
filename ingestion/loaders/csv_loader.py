from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

from ingestion.loaders.base import BaseLoader


class CSVLoader(BaseLoader):
    """Chunked CSV reader so large files never load fully into memory."""

    def iter_batches(self, path: Path) -> Iterator[list[dict[str, Any]]]:
        for chunk in pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
            chunksize=self.batch_size,
        ):
            records = chunk.to_dict(orient="records")
            for record in records:
                for key, value in record.items():
                    # pandas leaves missing cells as float NaN even under
                    # dtype=str; normalize to None (JSON/SQL have no NaN).
                    if isinstance(value, float) and pd.isna(value):
                        record[key] = None
            yield records
