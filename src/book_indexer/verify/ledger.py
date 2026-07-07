"""Evidence-ledger builder — writes artifacts/index_evidence.json.

The ledger is Phase 2's QUAL-01 byte-identical artifact (precedent for
Phase 6's 8-artifact gate) AND the VER-06 ship-blocker's source of truth.

Determinism contract (inherited from D-04):
  - For each term, verify() yields Evidence in (pdf_page ASC, token_offset ASC).
  - We accept verify()'s iteration order then re-sort the entire ledger by
    (canonical_term, pdf_page, token_offset) before emit — this guarantees
    stable cross-term ordering when `terms` is passed in varied shapes.
  - orjson.dumps(rows, option=OPT_SORT_KEYS | OPT_INDENT_2) — sorts keys
    alphabetically inside each row and indents so diffs are PR-reviewable.
  - No timestamps, no float rounding concerns (Evidence has no float fields).
  - Write is atomic: temp file + rename — if a process is killed mid-write,
    the shipped ledger never ends up partially written.

This module imports from .verifier (build_ledger calls verify() per term)
and from .evidence (load_ledger round-trips rows through Evidence). It
MUST NOT construct Evidence itself — Architecture Lock #1 allows exactly
one emitter in the project and that emitter is verify().
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

import orjson

from .evidence import Evidence
from .verifier import verify

_LEDGER_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2


def build_ledger(
    conn: sqlite3.Connection,
    terms: list[str],
    out_path: Path,
    *,
    variants_for: Callable[[str], list[str]] | None = None,
) -> int:
    """Run verify() for every term, collect Evidence, write sorted JSON.

    Byte-identical across runs (QUAL-01 precedent). Returns the number of
    Evidence rows written.

    Args:
        conn: read-only SQLite connection to the corpus.
        terms: iterable of canonical terms to verify. De-duplicated internally
               via ``sorted(set(terms))`` so duplicate input produces identical
               output bytes.
        out_path: destination Path for the JSON file. Parent directories are
                  created if needed. Write is atomic (temp + rename).
        variants_for: optional acronym-variants callable passed through to
                      verify(). Usually None in Phase 2.

    Returns:
        int: number of Evidence rows written.
    """
    rows: list[dict] = []
    for term in sorted(set(terms)):
        for ev in verify(term, conn, variants_for=variants_for):
            rows.append(ev.model_dump(mode="json"))

    # Cross-term ordering contract. Evidence.section_path is a tuple that
    # model_dump(mode="json") converts to a list, so the sort key uses only
    # hashable primitives.
    rows.sort(key=lambda r: (r["canonical_term"], r["pdf_page"], r["token_offset"]))

    data = orjson.dumps(rows, option=_LEDGER_OPTS)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: temp file in the same directory, then os.replace (atomic
    # on POSIX when on the same filesystem). Prevents half-written ledgers.
    fd, tmp_str = tempfile.mkstemp(
        prefix=out_path.name + ".",
        suffix=".tmp",
        dir=str(out_path.parent),
    )
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, out_path)
    except Exception:
        # Best-effort cleanup of the temp file if the rename failed.
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return len(rows)


def load_ledger(path: Path) -> list[Evidence]:
    """Load a ledger JSON file back into Evidence objects (round-trip check).

    Not called by build_ledger; provided for tests and downstream consumers
    that want to stream the shipped ledger as typed Evidence objects.
    """
    import json

    with Path(path).open("r", encoding="utf-8") as f:
        rows = json.load(f)
    return [Evidence.model_validate(r) for r in rows]
