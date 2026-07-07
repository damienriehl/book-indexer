"""Unit tests for ``src/book_indexer/concepts/union.py``.

Covers (v2 — 3-pass shape):
- canonical_form_key fallback (nlp=None).
- empty-results, single-pass, cross-pass merge, null-response skip.
- H-9 sorted-at-boundary (tuple[str, ...] sorted by PASS_ORDER.index,
  not alphabetical).
- D-13 provenance preservation (all raw candidates retained).
- frozen PoolEntry.
- deterministic union across repeated runs.

Plan v2-03 Task 2 update: PASS_ORDER drops "implicit"; tests that
referenced 4 passes now reference 3. CallResult uses the v2 4-field
shape (chunk_id, pass_type, response, error).

requirements_addressed: CON-04, CON-05
"""
from __future__ import annotations

import pytest

from book_indexer.concepts import ConceptCandidate, ConceptDiscoveryResponse
from book_indexer.concepts.passes import PASS_ORDER, CallResult
from book_indexer.concepts.union import PoolEntry, canonical_form_key, union_candidates


def _mk_result(
    chunk_id: str,
    pass_type: str,
    canonical: str,
    term: str | None = None,
) -> CallResult:
    resp = ConceptDiscoveryResponse(
        schema_version="1",
        pass_type=pass_type,  # type: ignore[arg-type]
        chunk_id=chunk_id,
        candidates=[
            ConceptCandidate(
                term=term or canonical,
                canonical_form=canonical,
                variants=[],
                example_quote="quote",
            )
        ],
    )
    return CallResult(
        chunk_id=chunk_id,
        pass_type=pass_type,
        response=resp,
        error=None,
    )


# ---------------------------------------------------------------------------
# PASS_ORDER shape (v2 — drops "implicit")
# ---------------------------------------------------------------------------


def test_pass_order_is_three_passes_v2() -> None:
    """v2 PASS_ORDER: noun_phrase < doctrinal < ner; implicit dropped."""
    assert PASS_ORDER == ("noun_phrase", "doctrinal", "ner")
    assert "implicit" not in PASS_ORDER
    assert len(PASS_ORDER) == 3


# ---------------------------------------------------------------------------
# canonical_form_key
# ---------------------------------------------------------------------------


def test_canonical_form_key_lowercase_strip() -> None:
    assert canonical_form_key("  Voir Dire  ") == "voir dire"
    assert canonical_form_key("HEARSAY") == "hearsay"
    assert canonical_form_key("Confrontation Clause") == "confrontation clause"


# ---------------------------------------------------------------------------
# Empty / single-pass / multi-pass merges
# ---------------------------------------------------------------------------


def test_union_empty_results() -> None:
    assert union_candidates([]) == {}


def test_union_single_pass_single_candidate() -> None:
    pool = union_candidates([_mk_result("ch1", "doctrinal", "hearsay")])
    assert set(pool.keys()) == {"hearsay"}
    entry = pool["hearsay"]
    assert entry.passes == ("doctrinal",)
    assert len(entry.candidates) == 1
    assert entry.canonical_form_key == "hearsay"


def test_union_same_canonical_across_passes_merges() -> None:
    results = [
        _mk_result("ch1", "doctrinal", "hearsay"),
        _mk_result("ch1", "noun_phrase", "hearsay"),
    ]
    pool = union_candidates(results)
    entry = pool["hearsay"]
    # Sorted by PASS_ORDER.index: noun_phrase(0) < doctrinal(1).
    assert entry.passes == ("noun_phrase", "doctrinal")
    assert len(entry.candidates) == 2


def test_union_passes_tuple_sorted_by_PASS_ORDER_not_alphabetical() -> None:
    """H-9 specific hazard: alphabetical sort of v2 passes is
    ``doctrinal < ner < noun_phrase``, but PASS_ORDER is literal
    ``('noun_phrase', 'doctrinal', 'ner')``."""
    results = [
        _mk_result("ch1", "ner", "foo"),
        _mk_result("ch1", "doctrinal", "foo"),
        _mk_result("ch1", "noun_phrase", "foo"),
    ]
    pool = union_candidates(results)
    assert pool["foo"].passes == ("noun_phrase", "doctrinal", "ner"), pool["foo"].passes
    # Confirm NOT alphabetical:
    assert pool["foo"].passes != tuple(sorted(["ner", "doctrinal", "noun_phrase"]))
    # Length is 3 in v2 (was 4 with "implicit").
    assert len(pool["foo"].passes) == 3


def test_union_passes_tuple_type_is_tuple_not_set() -> None:
    """H-9 structural check: PoolEntry.passes must be tuple[str, ...]."""
    pool = union_candidates([_mk_result("ch1", "doctrinal", "hearsay")])
    entry = pool["hearsay"]
    assert isinstance(entry.passes, tuple)
    assert not isinstance(entry.passes, set)


def test_union_passes_tuple_is_deduped() -> None:
    """Two results from the SAME pass should collapse to a single tuple entry."""
    results = [
        _mk_result("ch1", "doctrinal", "hearsay"),
        _mk_result("ch2", "doctrinal", "hearsay"),
    ]
    pool = union_candidates(results)
    entry = pool["hearsay"]
    assert entry.passes == ("doctrinal",)
    # Both candidates preserved in candidates tuple (D-13 provenance).
    assert len(entry.candidates) == 2
    # Cap at 3 in v2 (was 4 with "implicit").
    assert len(entry.passes) <= 3


def test_union_passes_tuple_caps_at_three_v2() -> None:
    """All three v2 PASS_ORDER values can populate one canonical."""
    results = [
        _mk_result("ch1", p, "foo") for p in ("noun_phrase", "doctrinal", "ner")
    ]
    pool = union_candidates(results)
    assert pool["foo"].passes == ("noun_phrase", "doctrinal", "ner")
    assert len(pool["foo"].passes) == 3
    assert len(pool["foo"].passes) <= 3


# ---------------------------------------------------------------------------
# D-13 provenance preservation
# ---------------------------------------------------------------------------


def test_union_preserves_all_raw_candidates() -> None:
    """D-13: every raw ConceptCandidate is preserved for Phase 4 tiebreakers."""
    results = [
        _mk_result("ch1", "doctrinal", "hearsay", term="hearsay"),
        _mk_result("ch2", "doctrinal", "hearsay", term="Hearsay"),
        _mk_result("ch3", "noun_phrase", "hearsay", term="hearsays"),
    ]
    pool = union_candidates(results)
    entry = pool["hearsay"]
    assert len(entry.candidates) == 3
    terms = {c.term for c in entry.candidates}
    assert terms == {"hearsay", "Hearsay", "hearsays"}


def test_union_candidates_is_tuple_not_list() -> None:
    """PoolEntry.candidates is immutable (frozen slot guarantee)."""
    pool = union_candidates([_mk_result("ch1", "doctrinal", "hearsay")])
    assert isinstance(pool["hearsay"].candidates, tuple)


# ---------------------------------------------------------------------------
# Null-response skipping
# ---------------------------------------------------------------------------


def test_union_skips_null_response() -> None:
    null = CallResult(
        chunk_id="ch1",
        pass_type="ner",
        response=None,
        error="extraction failed: simulated",
    )
    pool = union_candidates([null])
    assert pool == {}


def test_union_mixes_successes_and_failures() -> None:
    good = _mk_result("ch1", "doctrinal", "hearsay")
    bad = CallResult(
        chunk_id="ch2",
        pass_type="ner",
        response=None,
        error="ValueError: simulated",
    )
    pool = union_candidates([good, bad])
    assert set(pool.keys()) == {"hearsay"}
    assert pool["hearsay"].passes == ("doctrinal",)


# ---------------------------------------------------------------------------
# Frozen + determinism
# ---------------------------------------------------------------------------


def test_union_pool_entry_is_frozen() -> None:
    pool = union_candidates([_mk_result("ch1", "doctrinal", "hearsay")])
    entry = pool["hearsay"]
    with pytest.raises((AttributeError, TypeError)):
        entry.passes = ("other",)  # type: ignore[misc]


def test_union_deterministic_across_runs() -> None:
    """Two union calls on the same inputs produce the same key iteration
    order AND the same tuple contents — byte-identical dicts."""
    results = [
        _mk_result("ch2", "doctrinal", "hearsay"),
        _mk_result("ch1", "noun_phrase", "Voir Dire"),
        _mk_result("ch3", "ner", "Miranda warning"),
    ]
    pool_a = union_candidates(results)
    pool_b = union_candidates(results)
    assert list(pool_a.keys()) == list(pool_b.keys())
    for k in pool_a:
        assert pool_a[k].passes == pool_b[k].passes
        assert pool_a[k].candidates == pool_b[k].candidates
        assert pool_a[k].canonical_form_key == pool_b[k].canonical_form_key


def test_union_key_order_is_sorted() -> None:
    """Dict iteration walks keys in sorted() order (defensive determinism)."""
    results = [
        _mk_result("ch1", "doctrinal", "zulu"),
        _mk_result("ch1", "doctrinal", "alpha"),
        _mk_result("ch1", "doctrinal", "mike"),
    ]
    pool = union_candidates(results)
    assert list(pool.keys()) == ["alpha", "mike", "zulu"]


def test_union_canonical_form_key_case_folded() -> None:
    """Different-cased canonical_forms collapse to one entry (fallback path)."""
    results = [
        _mk_result("ch1", "doctrinal", "Hearsay"),
        _mk_result("ch1", "noun_phrase", "hearsay"),
        _mk_result("ch1", "ner", "HEARSAY"),
    ]
    pool = union_candidates(results)
    # All three collapse to the lowercased-stripped "hearsay" key.
    assert list(pool.keys()) == ["hearsay"]
    entry = pool["hearsay"]
    assert entry.passes == ("noun_phrase", "doctrinal", "ner")
    assert len(entry.candidates) == 3


# ---------------------------------------------------------------------------
# PoolEntry dataclass contract
# ---------------------------------------------------------------------------


def test_pool_entry_fields() -> None:
    e = PoolEntry(
        canonical_form_key="foo",
        passes=("doctrinal",),
        candidates=(
            ConceptCandidate(
                term="foo", canonical_form="foo", variants=[],
                example_quote="quote",
            ),
        ),
    )
    assert e.canonical_form_key == "foo"
    assert e.passes == ("doctrinal",)
    assert len(e.candidates) == 1
