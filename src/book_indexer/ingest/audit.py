"""Deterministic audit-artifact writer (orjson sorted keys)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import orjson


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Write rows as newline-delimited JSON with sorted keys.

    Per D-20: orjson + OPT_SORT_KEYS is the only JSON writer in the project.
    Caller is responsible for row order (sort deterministically before passing).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        for row in rows:
            f.write(orjson.dumps(row, option=orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE))
