"""Unit tests for book_indexer.assembly.verifier_sweep.

requirements_addressed: ASM-02 (every (canonical, variant) pair runs
verify() exactly once to enumerate every Evidence; only verified
occurrences become index citations).

Per Plan 04-02:
- verifier_sweep.py is the SOLE Phase 4 module that calls verify() (Lock #1).
- ProcessPoolExecutor with workers=8 (RESEARCH §H-4 — ThreadPool is empirically
  WORSE than sequential for this workload).
- Each worker opens its own sqlite3.Connection (Connection is not shareable
  across processes).
- Per-canonical Evidence list is deduplicated by (section_ref, folio); lowest
  (pdf_page, token_offset) representative kept.
- Output Evidence rows are sorted by (section_ref, folio) ascending.
- verifier_sweep does NOT construct Evidence(...) directly — Lock #1.
- run_sweep called twice with the same buckets returns byte-identical Evidence
  lists (Lock #5 determinism).

These tests use the live ``artifacts/page_corpus.sqlite`` (Phase 1 deliverable).
If absent, every live-corpus sub-test skips with a clear message.
"""
from __future__ import annotations

from pathlib import Path

import orjson
import pytest

from book_indexer.assembly.dedup import BucketCandidate
from book_indexer.assembly.verifier_sweep import (
    EvidenceByCanonical,
    _sweep_one,
    run_sweep,
    sweep_canonical,
)

CORPUS = Path("artifacts/page_corpus.sqlite")


def _corpus_or_skip() -> str:
    if not CORPUS.exists():
        pytest.skip(f"missing {CORPUS} (Phase 1 deliverable)")
    return str(CORPUS)


# ---------------------------------------------------------------------------
# Behavior 1: _sweep_one returns Evidence list for a known term.
# ---------------------------------------------------------------------------


def test_sweep_one_voir_dire_returns_nonempty():
    """A known-the reference corpus term yields >= 1 Evidence row."""
    corpus = _corpus_or_skip()
    evs = _sweep_one(("voir dire", ["voir dire"], corpus))
    assert evs, "voir dire should appear in the reference corpus"
    assert all(e.canonical_term == "voir dire" for e in evs)


# ---------------------------------------------------------------------------
# Behavior 2: empty variants list falls back to single-canonical verify call.
# ---------------------------------------------------------------------------


def test_sweep_one_empty_variants_falls_back_to_canonical():
    """Empty variants means verify() is called with [canonical] only."""
    corpus = _corpus_or_skip()
    evs = _sweep_one(("voir dire", [], corpus))
    assert evs, "empty variants should still hit the canonical surface"
    assert all(e.canonical_term == "voir dire" for e in evs)


# ---------------------------------------------------------------------------
# Behavior 3: a canonical with no body matches returns [].
# ---------------------------------------------------------------------------


def test_sweep_one_unknown_term_returns_empty():
    """A term that does not occur in the corpus returns []."""
    corpus = _corpus_or_skip()
    evs = _sweep_one(
        ("xyzzy_definitely_not_in_corpus", ["xyzzy_definitely_not_in_corpus"], corpus)
    )
    assert evs == []


# ---------------------------------------------------------------------------
# Behavior 4: run_sweep with N buckets returns dict with N keys preserved.
# ---------------------------------------------------------------------------


def test_run_sweep_preserves_keys_and_order():
    """run_sweep over 5 buckets returns 5 keys in input order."""
    corpus = _corpus_or_skip()
    buckets = {
        f"bkt-{i}-{term}": BucketCandidate(
            lemma_key=f"bkt-{i}-{term}",
            surfaces=[term],
        )
        for i, term in enumerate(
            ["voir dire", "objection", "hearsay", "witness", "jury"]
        )
    }

    def chooser(b: BucketCandidate) -> str:
        return b.surfaces[0]

    results = run_sweep(buckets, chooser, corpus, max_workers=2)
    assert isinstance(results, dict)
    assert list(results.keys()) == list(buckets.keys())
    assert len(results) == 5


# ---------------------------------------------------------------------------
# Behavior 5: determinism — two consecutive runs are byte-identical.
# ---------------------------------------------------------------------------


def test_run_sweep_is_deterministic():
    """Two consecutive run_sweep calls produce byte-identical Evidence lists."""
    corpus = _corpus_or_skip()
    buckets = {
        f"bkt-{i}-{term}": BucketCandidate(
            lemma_key=f"bkt-{i}-{term}",
            surfaces=[term],
            variants=[term + "s"] if i % 2 == 0 else [],
        )
        for i, term in enumerate(["voir dire", "objection", "witness"])
    }

    def chooser(b: BucketCandidate) -> str:
        return b.surfaces[0]

    r1 = run_sweep(buckets, chooser, corpus, max_workers=2)
    r2 = run_sweep(buckets, chooser, corpus, max_workers=2)

    # orjson-serialize each Evidence list; compare bytes.
    def dump(d: EvidenceByCanonical) -> bytes:
        return orjson.dumps(
            {k: [e.model_dump(mode="json") for e in v] for k, v in d.items()}
        )

    assert dump(r1) == dump(r2)


# ---------------------------------------------------------------------------
# Behavior 6: nonexistent corpus_path raises (sqlite3.OperationalError or similar).
# ---------------------------------------------------------------------------


def test_sweep_one_nonexistent_corpus_raises():
    """Passing a nonexistent corpus_path propagates the sqlite error."""
    import sqlite3

    with pytest.raises((sqlite3.OperationalError, sqlite3.DatabaseError)):
        _sweep_one(("voir dire", ["voir dire"], "/nonexistent/corpus.sqlite"))


# ---------------------------------------------------------------------------
# Behavior 7: dedup-by-(section_ref, folio) keeps lowest (pdf_page, token_offset)
# representative.
#
# Strategy: pick a term that appears multiple times within a single section.
# Then assert that for each (section_ref, folio) pair there is exactly one
# Evidence row (no duplicates).
# ---------------------------------------------------------------------------


def test_sweep_one_dedupes_by_section_and_folio():
    """Each (section_ref, folio) pair appears at most once in the output."""
    corpus = _corpus_or_skip()
    evs = _sweep_one(("voir dire", ["voir dire"], corpus))
    seen: set[tuple[str, str]] = set()
    for ev in evs:
        key = (ev.section_ref, ev.folio)
        assert key not in seen, f"duplicate (section_ref, folio): {key}"
        seen.add(key)


# ---------------------------------------------------------------------------
# Behavior 8: output is sorted by (section_ref, folio) ascending.
# ---------------------------------------------------------------------------


def test_sweep_one_sorted_by_section_ref_and_folio():
    """Output Evidence list is sorted by (section_ref, folio) asc — NOT pdf_page."""
    corpus = _corpus_or_skip()
    evs = _sweep_one(("voir dire", ["voir dire"], corpus))
    keys = [(e.section_ref, e.folio) for e in evs]
    assert keys == sorted(keys), f"not sorted: {keys}"


# ---------------------------------------------------------------------------
# Behavior 9: sweep_canonical is the public single-canonical wrapper.
# ---------------------------------------------------------------------------


def test_sweep_canonical_matches_sweep_one():
    """sweep_canonical(...) returns the same result as _sweep_one((...))."""
    corpus = _corpus_or_skip()
    evs_a = sweep_canonical("voir dire", ["voir dire"], corpus)
    evs_b = _sweep_one(("voir dire", ["voir dire"], corpus))
    # Compare via model_dump for stable equality.
    assert [e.model_dump(mode="json") for e in evs_a] == [
        e.model_dump(mode="json") for e in evs_b
    ]


# ---------------------------------------------------------------------------
# Behavior 10: empty buckets dict → empty result dict (no ProcessPool spin-up).
# ---------------------------------------------------------------------------


def test_run_sweep_empty_buckets_returns_empty_dict():
    """run_sweep({}, ...) returns {} without raising."""
    result = run_sweep({}, lambda b: b.surfaces[0], "/whatever/path.sqlite")
    assert result == {}


# ---------------------------------------------------------------------------
# Lock #1: verifier_sweep.py contains ZERO Evidence(...) constructions.
# ---------------------------------------------------------------------------


def test_verifier_sweep_does_not_construct_evidence():
    """grep-style scan: no `Evidence(` constructor calls in verifier_sweep.py.

    Allow the import line; reject any actual constructor call.
    """
    src = Path("src/book_indexer/assembly/verifier_sweep.py").read_text()
    for lineno, line in enumerate(src.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("from") or stripped.startswith("import"):
            continue
        # Strip comments before scanning so docstrings don't false-positive.
        if stripped.startswith("#"):
            continue
        assert "Evidence(" not in line, (
            f"Lock #1 violation at line {lineno}: {line!r}"
        )


# ---------------------------------------------------------------------------
# Lock #1: verifier_sweep.py is the ONLY assembly/ module importing verify.
# ---------------------------------------------------------------------------


def test_verifier_sweep_is_sole_verify_consumer_in_assembly():
    """grep -rn 'from book_indexer.verify import verify' src/book_indexer/assembly/
    must return exactly one file: verifier_sweep.py.
    """
    import re

    asm_dir = Path("src/book_indexer/assembly")
    pattern = re.compile(
        r"from\s+book_indexer\.verify\s+import\s+.*\bverify\b"
    )
    hits: list[Path] = []
    for f in asm_dir.rglob("*.py"):
        if pattern.search(f.read_text()):
            hits.append(f)

    assert len(hits) == 1, f"expected exactly 1 verify importer, got: {hits}"
    assert hits[0].name == "verifier_sweep.py", (
        f"only verifier_sweep.py may import verify(), got: {hits[0]}"
    )


# ---------------------------------------------------------------------------
# Forbidden: ThreadPoolExecutor (RESEARCH §H-4 — empirically worse than sequential).
# ---------------------------------------------------------------------------


def test_verifier_sweep_uses_processpool_not_threadpool():
    """Module imports/uses ProcessPoolExecutor; never ThreadPool* in code.

    RESEARCH §H-4 line 396-397: ThreadPool is empirically WORSE than
    sequential for this workload (sqlite GIL contention). Docstring
    mentions of ThreadPool are allowed (they document the prohibition);
    actual code references are forbidden.
    """
    import ast

    src = Path("src/book_indexer/assembly/verifier_sweep.py").read_text()
    assert "ProcessPoolExecutor" in src

    tree = ast.parse(src)

    # AST-based scan: gather every Name / Attribute / ImportFrom symbol;
    # those are the "real" code uses (docstrings are ast.Constant strings
    # inside ast.Expr, never symbols).
    symbols: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            symbols.add(node.id)
        elif isinstance(node, ast.Attribute):
            symbols.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                symbols.add(alias.name)
                if alias.asname:
                    symbols.add(alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.add(alias.name)
                if alias.asname:
                    symbols.add(alias.asname)

    assert "ProcessPoolExecutor" in symbols, (
        "verifier_sweep.py must use ProcessPoolExecutor"
    )
    assert "ThreadPoolExecutor" not in symbols, (
        "RESEARCH §H-4 forbids ThreadPoolExecutor"
    )
    assert "ThreadPool" not in symbols
