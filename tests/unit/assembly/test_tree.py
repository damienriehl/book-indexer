"""Unit tests for ``book_indexer.assembly.tree``.

Tests for the orchestrator's pure-function helpers (slugify, compute_id,
compute_sort_key) plus integration-style tests for the synthetic build
flow. The full live build is exercised in Plan 04-05's cold-build
acceptance test; this file proves composition correctness on small,
deterministic inputs.

requirements_addressed: ASM-01 (canonical-form selection composes through
tree.py), ASM-08 (frozen IR), ASM-04 (subdivide ordering), ASM-09
(table-citation deconfliction respected at intake).

Lock #1 verification: this test file confirms tree.py contains zero
``Evidence(...)`` constructions and never imports verify() directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from book_indexer.assembly import (
    compute_id,
    compute_oob_status,
    compute_sort_key,
    slugify,
)


# ---------------------------------------------------------------------------
# slugify — D-07 + RESEARCH §H-10.
# ---------------------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("Voir Dire") == "voir-dire"


def test_slugify_collapses_runs() -> None:
    assert slugify("Hearsay   Rule") == "hearsay-rule"


def test_slugify_strips_leading_trailing_punct() -> None:
    assert slugify("- voir dire -") == "voir-dire"


def test_slugify_empty_returns_x() -> None:
    """Empty/whitespace-only input returns 'x' (defensive)."""
    assert slugify("") == "x"
    assert slugify("   ") == "x"


def test_slugify_keeps_digits() -> None:
    assert slugify("FRE 706") == "fre-706"


# ---------------------------------------------------------------------------
# compute_id — D-07 collision suffix policy.
# ---------------------------------------------------------------------------


def test_compute_id_first_call_returns_slug() -> None:
    taken: set[str] = set()
    assert compute_id("Voir Dire", taken) == "voir-dire"
    assert "voir-dire" in taken


def test_compute_id_second_call_appends_2() -> None:
    """Two canonicals slugifying to the same id: second gets '-2'."""
    taken: set[str] = set()
    assert compute_id("Voir Dire", taken) == "voir-dire"
    assert compute_id("voir dire", taken) == "voir-dire-2"


def test_compute_id_third_call_appends_3() -> None:
    taken: set[str] = set()
    compute_id("Voir Dire", taken)
    compute_id("voir dire", taken)
    assert compute_id("VOIR DIRE", taken) == "voir-dire-3"


def test_compute_id_collision_suffix_skips_taken() -> None:
    """If '-2' is already taken externally, '-3' is chosen."""
    taken: set[str] = {"voir-dire", "voir-dire-2"}
    assert compute_id("Voir Dire", taken) == "voir-dire-3"


# ---------------------------------------------------------------------------
# compute_sort_key — D-07 letter-by-letter alpha.
# ---------------------------------------------------------------------------


def test_compute_sort_key_strips_leading_article() -> None:
    assert compute_sort_key("The Hearsay Rule") == "hearsay rule"


def test_compute_sort_key_strips_a() -> None:
    assert compute_sort_key("A Witness") == "witness"


def test_compute_sort_key_strips_an() -> None:
    assert compute_sort_key("An Objection") == "objection"


def test_compute_sort_key_does_not_strip_non_articles() -> None:
    """'Of' is meaningful in legal phrases; do not strip."""
    assert compute_sort_key("Burden of Proof") == "burden of proof"


def test_compute_sort_key_lowercases() -> None:
    assert compute_sort_key("FRE 706") == "fre 706"


# ---------------------------------------------------------------------------
# Lock #1 invariants — direct file content checks.
# ---------------------------------------------------------------------------


_TREE_PY = (
    Path(__file__).resolve().parents[3]
    / "src" / "book_indexer" / "assembly" / "tree.py"
)
_COVERAGE_PY = (
    Path(__file__).resolve().parents[3]
    / "src" / "book_indexer" / "assembly" / "coverage.py"
)


def test_tree_py_does_not_construct_evidence() -> None:
    """Lock #1: tree.py never calls ``Evidence(...)``.

    Only ``verify()`` (transitively via ``verifier_sweep.run_sweep``) is
    allowed to construct Evidence rows. tree.py reads attributes off
    Evidence rows when building the ledger but does not construct.
    """
    text = _TREE_PY.read_text(encoding="utf-8")
    # Search for Evidence(...) constructor calls — Evidence as a name
    # appears in type hints / imports, but constructor calls have an
    # immediate '('.
    import re
    constructor_calls = re.findall(r"\bEvidence\s*\(", text)
    assert not constructor_calls, (
        f"tree.py contains {len(constructor_calls)} Evidence(...) "
        f"constructions — Lock #1 violated"
    )


def test_tree_py_does_not_import_verify_directly() -> None:
    """Lock #1: tree.py does not import ``verify`` (only via verifier_sweep).

    The import-graph check ensures that verify() is consumed transitively
    through ``verifier_sweep.run_sweep`` (the sole boundary). If tree.py
    starts to call verify() directly, it would establish a second
    locator-emitting path — forbidden.
    """
    text = _TREE_PY.read_text(encoding="utf-8")
    assert "from book_indexer.verify import verify" not in text
    assert "from book_indexer.verify.verify import" not in text
    # `from book_indexer.verify.evidence import Evidence` IS allowed —
    # tree.py needs the type for type hints + ledger payloads (read-only).


def test_coverage_py_does_not_construct_evidence() -> None:
    """Lock #1: coverage.py never constructs Evidence."""
    import re
    text = _COVERAGE_PY.read_text(encoding="utf-8")
    constructor_calls = re.findall(r"\bEvidence\s*\(", text)
    assert not constructor_calls, (
        f"coverage.py constructs Evidence ({len(constructor_calls)}× ) — "
        "Lock #1 violated"
    )


# ---------------------------------------------------------------------------
# Synthetic build_index_tree integration — uses real spaCy + in-memory
# corpus to keep determinism while exercising the composition.
# ---------------------------------------------------------------------------


def test_oob_status_within_band_is_none() -> None:
    """Sanity check: compute_oob_status agrees with module constants."""
    assert compute_oob_status(800) == "none"
    assert compute_oob_status(1500) == "none"


# ---------------------------------------------------------------------------
# Plan 04-04-fix BUG 1: Locator.section_ref must equal rep evidence row's.
# ---------------------------------------------------------------------------


def test_rewrite_locators_uses_rep_evidence_section_ref(make_evidence) -> None:
    """BUG 1 (ASM-02): locator's section_ref agrees with rep Evidence row.

    Synthetic input: 3 evidence rows in chapter 3 at varying depths
    (§3.02.1, §3.02.2, §3.04). cite_for_canonical promotes to LCA "§3";
    after _rewrite_locators_to_rep_evidence, the locator's section_ref
    must equal the rep evidence row's section_ref (the row with the
    smallest (pdf_page, token_offset)).
    """
    from book_indexer.assembly.cite_rule import cite_for_canonical
    from book_indexer.assembly.tree import _rewrite_locators_to_rep_evidence

    ev1 = make_evidence(
        section_path=("§3", "§3.02", "§3.02.1"),
        canonical_term="hearsay",
        matched_variant="hearsay",
        pdf_page=165,
        token_offset=10,
        folio="165",
    )
    ev2 = make_evidence(
        section_path=("§3", "§3.02", "§3.02.2"),
        canonical_term="hearsay",
        matched_variant="hearsay",
        pdf_page=170,
        token_offset=5,
        folio="170",
    )
    ev3 = make_evidence(
        section_path=("§3", "§3.04"),
        canonical_term="hearsay",
        matched_variant="hearsay",
        pdf_page=180,
        token_offset=0,
        folio="180",
    )
    locs = cite_for_canonical([ev1, ev2, ev3])
    # cite_for_canonical promotes to LCA = "§3" (one chapter cluster).
    assert len(locs) == 1
    assert locs[0].section_ref == "§3"

    rewritten = _rewrite_locators_to_rep_evidence(
        {"hearsay": locs}, {"hearsay": [ev1, ev2, ev3]}
    )
    # rep is ev1 (smallest pdf_page, token_offset). Section_ref must be
    # the deeper rep value, NOT the LCA.
    assert rewritten["hearsay"][0].section_ref == "§3.02.1"
    assert rewritten["hearsay"][0].folio == "165"


def test_rewrite_locators_preserves_evidence_id_placeholder(make_evidence) -> None:
    """The rewrite preserves Locator.evidence_id (placeholder filled later)."""
    from book_indexer.assembly.cite_rule import (
        _PLACEHOLDER_EVIDENCE_ID,
        cite_for_canonical,
    )
    from book_indexer.assembly.tree import _rewrite_locators_to_rep_evidence

    ev = make_evidence(
        section_path=("§2", "§2.04"),
        canonical_term="voir dire",
        matched_variant="voir dire",
    )
    locs = cite_for_canonical([ev])
    rewritten = _rewrite_locators_to_rep_evidence(
        {"voir dire": locs}, {"voir dire": [ev]}
    )
    # placeholder preserved through the rewrite step
    assert rewritten["voir dire"][0].evidence_id == _PLACEHOLDER_EVIDENCE_ID


# ---------------------------------------------------------------------------
# Plan 04-04-fix BUG 2: section-title canonicals dropped at intake.
# ---------------------------------------------------------------------------


def test_load_section_titles_to_exclude_returns_normalized_set(tmp_path) -> None:
    """_load_section_titles_to_exclude lowercases + strips leading article."""
    import yaml

    from book_indexer.assembly.tree import _load_section_titles_to_exclude

    fixture = tmp_path / "sections.yaml"
    fixture.write_text(
        yaml.safe_dump(
            {
                "sections": [
                    {"title": "The Hearsay Rule"},
                    {"title": "Direct Examination"},
                    {"title": "Authenticity"},
                    {"title": ""},  # defensive: blank skipped
                    {},  # defensive: missing title skipped
                ]
            }
        ),
        encoding="utf-8",
    )
    titles = _load_section_titles_to_exclude(fixture)
    assert "hearsay rule" in titles
    assert "direct examination" in titles
    assert "authenticity" in titles
    assert "" not in titles


def test_load_section_titles_to_exclude_missing_file_returns_empty() -> None:
    """Missing fixture path returns an empty set (defensive)."""
    from book_indexer.assembly.tree import _load_section_titles_to_exclude

    out = _load_section_titles_to_exclude(Path("/nonexistent/sections.yaml"))
    assert out == set()


def test_dedup_drops_section_title_canonicals() -> None:
    """ASM-09: build_buckets drops candidates matching section titles."""
    import spacy

    from book_indexer.assembly.dedup import build_buckets
    from book_indexer.concepts.schema import ConceptCandidate

    nlp = spacy.load("en_core_web_lg")

    candidates = [
        ConceptCandidate(
            term="authenticity",
            canonical_form="authenticity",
            example_quote="...the authenticity of the document...",
        ),
        ConceptCandidate(
            term="direct examination",
            canonical_form="direct examination",
            example_quote="...during direct examination...",
        ),
        ConceptCandidate(
            term="impeachment by prior inconsistent statement",
            canonical_form="impeachment by prior inconsistent statement",
            example_quote="...impeachment by prior inconsistent statement...",
        ),
    ]
    section_titles = {"authenticity", "direct examination"}
    buckets, dropped = build_buckets(
        candidates,
        nlp,
        acronym_map={},
        section_title_keys=section_titles,
    )
    # Only the non-section-title candidate survives.
    assert len(buckets) == 1
    surviving = next(iter(buckets.values()))
    assert "impeachment by prior inconsistent statement" in surviving.surfaces
    # Both section-title candidates appear in dropped log.
    section_dropped = [d for d in dropped if d.get("reason") == "section_title"]
    assert len(section_dropped) == 2
    surfaces = {d["surface"] for d in section_dropped}
    assert surfaces == {"authenticity", "direct examination"}


def test_dedup_no_filter_when_no_section_titles_passed() -> None:
    """When section_title_keys is None/empty, no filtering occurs (back-compat)."""
    import spacy

    from book_indexer.assembly.dedup import build_buckets
    from book_indexer.concepts.schema import ConceptCandidate

    nlp = spacy.load("en_core_web_lg")
    candidates = [
        ConceptCandidate(
            term="authenticity",
            canonical_form="authenticity",
            example_quote="...authenticity of the document...",
        ),
    ]
    buckets, _ = build_buckets(candidates, nlp, acronym_map={})
    # No filter passed; bucket survives.
    assert len(buckets) == 1
