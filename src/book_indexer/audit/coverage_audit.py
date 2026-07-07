"""COV-01 audit helpers — count occurrences, classify hint, write deterministic outputs.

Sources:
    - 08-RESEARCH.md §Code Examples Ex 1 (verbatim ``_classify_hint`` / AUDIT_FIELDS)
    - 08-RESEARCH.md §Pitfall 4 (sensitivity filters; enforced at caller)
    - 08-RESEARCH.md §Don't Hand-Roll (stdlib ``csv.DictWriter`` + ``orjson``)

Determinism guarantees (Lock #5 extension to v1.2):
    - ``write_coverage_audit_json`` uses ``orjson.OPT_SORT_KEYS | OPT_INDENT_2``
      and pre-sorts entries by ``(candidate_term, discovered_via)``.
    - ``write_coverage_audit_csv`` uses ``csv.DictWriter`` with
      ``lineterminator='\n'`` and ``quoting=csv.QUOTE_MINIMAL``; same pre-sort.
    - Both writers call ``model_dump`` on frozen Pydantic models — no float
      serialization, no datetime, no env-dependent ordering.

Architecture Lock #1: this module never constructs ``Evidence``. It only
reads token counts from a read-only SQLite connection.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Iterable, Literal

import inflect
import orjson

from book_indexer.curator.fixture import CoverageAuditEntry

_INFLECT = inflect.engine()

# Field order = CSV column order (deterministic). JSON key order is overridden
# by orjson's OPT_SORT_KEYS — but AUDIT_FIELDS still pins the curator's
# expected schema-shape contract.
AUDIT_FIELDS: tuple[str, ...] = (
    "candidate_term",
    "discovered_via",
    "origin_chapter",
    "origin_pass",
    "literal_occurrence_count",
    "lemma_occurrence_count",
    "plural_form_occurrence_count",
    "nearest_section_heading_match",
    "classification_hint",
    "suggested_fix",
)


def count_occurrences(
    term: str,
    conn: sqlite3.Connection,
) -> tuple[int, int, int]:
    """Return ``(literal_count, lemma_count, plural_count)`` for the term's head word.

    The "head" is the last whitespace-delimited token of the term. The plural
    form is derived via ``inflect.engine().plural(head)`` (idempotent on
    already-plural input).

    All three counts are case-insensitive matches against ``tokens.text`` /
    ``tokens.lemma``. The connection MUST be opened with ``mode=ro``.
    """
    if not term or not term.strip():
        return (0, 0, 0)
    words = term.strip().split()
    head = words[-1].lower()
    plural_head = (_INFLECT.plural(head) or head).lower()
    cur = conn.cursor()
    literal_row = cur.execute(
        "SELECT COUNT(*) FROM tokens WHERE LOWER(text) = ?",
        (head,),
    ).fetchone()
    lemma_row = cur.execute(
        "SELECT COUNT(*) FROM tokens WHERE LOWER(lemma) = ?",
        (head,),
    ).fetchone()
    plural_row = cur.execute(
        "SELECT COUNT(*) FROM tokens WHERE LOWER(text) = ?",
        (plural_head,),
    ).fetchone()
    return (
        int(literal_row[0] if literal_row else 0),
        int(lemma_row[0] if lemma_row else 0),
        int(plural_row[0] if plural_row else 0),
    )


def derive_suggested_fix(
    literal: int,
    lemma: int,
    plural: int,
    term: str,
    head: str,
) -> tuple[
    Literal["lemma_collision", "bigram_inflected", "noise", "heading_only", "unknown"],
    Literal["lemma_override", "variant_loss_patch", "verify_patch", "none"],
]:
    """Return ``(classification_hint, suggested_fix)`` per the D-04 cascade.

    Heuristic table (RESEARCH §Code Examples Ex 1):
        - all-zero → ``("noise", "none")`` (curator → correct-rejection bucket)
        - plural > literal & plural > 0 → ``("bigram_inflected", "variant_loss_patch")``
        - literal=0 AND lemma>0 → ``("lemma_collision", "lemma_override")``
        - else → ``("unknown", "none")`` (curator decides; LLM may relabel in Wave 2)
    """
    if literal == 0 and lemma == 0 and plural == 0:
        return ("noise", "none")
    if plural > literal and plural > 0:
        return ("bigram_inflected", "variant_loss_patch")
    if literal == 0 and lemma > 0:
        return ("lemma_collision", "lemma_override")
    return ("unknown", "none")


def write_coverage_audit_json(
    entries: Iterable[CoverageAuditEntry],
    path: Path,
) -> None:
    """Deterministic JSON write: orjson sort_keys + indent=2; entries pre-sorted.

    Sort key is ``(candidate_term, discovered_via)`` — both are str fields
    locked by the schema, so the comparison is total and stable.
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: (e.candidate_term, e.discovered_via),
    )
    payload = [e.model_dump() for e in sorted_entries]
    path.write_bytes(
        orjson.dumps(payload, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
    )


def write_coverage_audit_csv(
    entries: Iterable[CoverageAuditEntry],
    path: Path,
) -> None:
    """Deterministic CSV: stdlib ``csv.DictWriter`` with ``lineterminator='\\n'``.

    Same pre-sort key as the JSON writer. ``None`` values render as empty
    strings (curator-friendly). Quoting is ``QUOTE_MINIMAL`` so values without
    commas / quotes / newlines are unquoted — keeps diffs readable.
    """
    sorted_entries = sorted(
        entries,
        key=lambda e: (e.candidate_term, e.discovered_via),
    )
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=list(AUDIT_FIELDS),
            quoting=csv.QUOTE_MINIMAL,
            lineterminator="\n",
        )
        writer.writeheader()
        for e in sorted_entries:
            row = e.model_dump()
            row = {k: ("" if row.get(k) is None else row.get(k)) for k in AUDIT_FIELDS}
            writer.writerow(row)
