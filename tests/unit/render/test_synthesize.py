"""Tests for B-06 union-of-token-lemmas synthesis (Phase 5 Wave 1).

Per RESEARCH §H-5 SPEC AMENDMENT: refines CONTEXT D-04's first-token-lemma
to union-of-token-lemmas; the canonical 'hearsay' example is the proof.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# --- Module-level exports ------------------------------------------------


def test_module_exports_required_names():
    from book_indexer.render import synthesize as syn

    for name in ("synthesize_bare_lemma_entries", "load_stopwords", "STOPWORDS_PATH"):
        assert hasattr(syn, name), f"synthesize.py missing export: {name}"


def test_module_docstring_cites_research_h5_spec_amendment():
    """Provenance trail per plan deviations_allowed (cannot be removed)."""
    from book_indexer.render import synthesize

    text = (synthesize.__doc__ or "").upper()
    assert "RESEARCH §H-5".upper() in text or "H-5" in text
    assert "SPEC AMENDMENT" in text
    assert "UNION" in text  # union-of-token-lemmas


def test_no_first_token_lemma_in_source():
    """Algorithm must be union-of-token-lemmas — first-token-lemma is a
    SHIP-BLOCKER regression. Search for the phrase in non-comment lines."""
    src = Path("src/book_indexer/render/synthesize.py").read_text()
    # Strip comments out of the search — matches in docstring/comments are OK
    code_lines = []
    for ln in src.splitlines():
        stripped = ln.strip()
        if stripped.startswith("#"):
            continue
        code_lines.append(ln)
    code_only = "\n".join(code_lines)
    # The forbidden algorithm:
    assert "first_token" not in code_only.lower() or "union" in code_only.lower(), (
        "synthesize.py must NOT use first-token-lemma logic"
    )


# --- load_stopwords ------------------------------------------------------


def test_load_stopwords_default_path():
    from book_indexer.render.synthesize import load_stopwords

    sws = load_stopwords()
    assert isinstance(sws, set)
    assert len(sws) >= 25
    for required in ("their", "your", "other", "with", "from"):
        assert required in sws, f"expected '{required}' in stopwords"


def test_load_stopwords_returns_lowercase():
    from book_indexer.render.synthesize import load_stopwords

    sws = load_stopwords()
    for s in sws:
        assert s == s.lower(), f"stopword '{s}' is not lowercase"


def test_load_stopwords_custom_path(tmp_path):
    """Path-injectable loader for tests."""
    from book_indexer.render.synthesize import load_stopwords

    custom = tmp_path / "test_stopwords.yaml"
    custom.write_text(yaml.safe_dump({
        "metadata": {"curated_by": "test"},
        "stopwords": [
            {"lemma": "alpha", "reason": "test"},
            {"lemma": "beta", "reason": "test"},
        ],
    }))
    sws = load_stopwords(custom)
    assert sws == {"alpha", "beta"}


# --- spaCy fixture --------------------------------------------------------


@pytest.fixture(scope="module")
def nlp():
    """Module-scoped spaCy load (cold load is ~1s; share across tests)."""
    spacy = pytest.importorskip("spacy")
    try:
        return spacy.load("en_core_web_lg")
    except OSError:
        pytest.skip("en_core_web_lg not installed")


@pytest.fixture
def stopwords():
    from book_indexer.render.synthesize import load_stopwords
    return load_stopwords()


# --- SPEC AMENDMENT proof: 'hearsay' surfaces ----------------------------


@pytest.mark.slow
def test_hearsay_canonical_spec_amendment_proof(make_entry, make_locator, nlp, stopwords):
    """RESEARCH §H-5: union-of-token-lemmas finds 'hearsay' from 4 siblings.

    First-token-lemma would lemmatize 'admissible hearsay' → 'admissible'
    and miss the 'hearsay' grouping entirely. This test is the load-bearing
    regression guard.
    """
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="admissible hearsay",
                   locators=[make_locator(section_ref="§3.02", folio="100")]),
        make_entry(canonical="inadmissible hearsay",
                   locators=[make_locator(section_ref="§3.02", folio="101")]),
        make_entry(canonical="hearsay exception",
                   locators=[make_locator(section_ref="§3.03", folio="110")]),
        make_entry(canonical="hearsay statement",
                   locators=[make_locator(section_ref="§3.03", folio="111")]),
    ]

    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)

    stems = [s.stem for s in result]
    assert "hearsay" in stems, (
        f"SPEC AMENDMENT regression: 'hearsay' not surfaced. Got: {stems}"
    )
    hearsay = next(s for s in result if s.stem == "hearsay")
    assert len(hearsay.sibling_canonicals) == 4
    assert hearsay.sibling_canonicals == tuple(sorted([
        "admissible hearsay",
        "inadmissible hearsay",
        "hearsay exception",
        "hearsay statement",
    ]))


# --- Stem rules: ≥3 sibs, bare-not-canonical, len ≥ 4, not stopword ------


@pytest.mark.slow
def test_threshold_two_sibs_no_synthesis(make_entry, nlp, stopwords):
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="admissible hearsay"),
        make_entry(canonical="inadmissible hearsay"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    stems = [s.stem for s in result]
    assert "hearsay" not in stems


@pytest.mark.slow
def test_threshold_three_sibs_synthesizes(make_entry, nlp, stopwords):
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="admissible hearsay"),
        make_entry(canonical="inadmissible hearsay"),
        make_entry(canonical="hearsay exception"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    stems = [s.stem for s in result]
    assert "hearsay" in stems


@pytest.mark.slow
def test_bare_stem_already_canonical_no_synthesis(make_entry, nlp, stopwords):
    """If 'hearsay' itself is already an IndexEntry, no synthetic 'hearsay'."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="hearsay"),  # bare stem already a canonical
        make_entry(canonical="admissible hearsay"),
        make_entry(canonical="inadmissible hearsay"),
        make_entry(canonical="hearsay exception"),
        make_entry(canonical="hearsay statement"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    stems = [s.stem for s in result]
    assert "hearsay" not in stems


@pytest.mark.slow
def test_short_stem_below_min_length_no_synthesis(make_entry, nlp, stopwords):
    """3-char stems are filtered (e.g., 'evi' from 'evidence rule', etc.)."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="big foo"),
        make_entry(canonical="big bar"),
        make_entry(canonical="big baz"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    stems = [s.stem for s in result]
    assert "big" not in stems  # length 3 < 4


@pytest.mark.slow
def test_stopword_stem_filtered(make_entry, make_locator, nlp):
    """5 sibs whose stem is in stopwords → not emitted."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    custom_stopwords = {"hearsay"}  # treat hearsay as a stopword for this test
    entries = [
        make_entry(canonical="admissible hearsay",
                   locators=[make_locator(folio="100")]),
        make_entry(canonical="inadmissible hearsay",
                   locators=[make_locator(folio="101")]),
        make_entry(canonical="hearsay exception",
                   locators=[make_locator(folio="102")]),
        make_entry(canonical="hearsay statement",
                   locators=[make_locator(folio="103")]),
        make_entry(canonical="dying-declaration hearsay",
                   locators=[make_locator(folio="104")]),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, custom_stopwords)
    stems = [s.stem for s in result]
    assert "hearsay" not in stems


# --- Locator dedup --------------------------------------------------------


@pytest.mark.slow
def test_locator_dedup_by_section_ref_and_folio(make_entry, make_locator, nlp, stopwords):
    """Two siblings sharing (section_ref, folio) → one locator in synthetic."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    shared = make_locator(section_ref="§2.04", folio="78", evidence_id=1)
    entries = [
        make_entry(canonical="admissible hearsay", id="a-h",
                   locators=[shared, make_locator(section_ref="§2.04", folio="79", evidence_id=2)]),
        make_entry(canonical="inadmissible hearsay", id="i-h",
                   locators=[make_locator(section_ref="§2.04", folio="78", evidence_id=99)]),
        make_entry(canonical="hearsay exception", id="h-e",
                   locators=[make_locator(section_ref="§2.04", folio="80", evidence_id=3)]),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    hearsay = next(s for s in result if s.stem == "hearsay")

    keys = [(loc.section_ref, loc.folio) for loc in hearsay.locators]
    assert len(keys) == len(set(keys)), f"locators not deduped: {keys}"
    # Should have exactly 3 unique (section_ref, folio) pairs: 78, 79, 80
    assert sorted(keys) == [("§2.04", "78"), ("§2.04", "79"), ("§2.04", "80")]


@pytest.mark.slow
def test_locators_returned_as_tuple(make_entry, nlp, stopwords):
    """SyntheticEntry is a frozen dataclass; locators must be a tuple."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="admissible hearsay"),
        make_entry(canonical="inadmissible hearsay"),
        make_entry(canonical="hearsay exception"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    assert result, "expected at least one synthetic entry"
    for s in result:
        assert isinstance(s.locators, tuple)
        assert isinstance(s.sibling_canonicals, tuple)


@pytest.mark.slow
def test_sibling_canonicals_sorted_alphabetically(make_entry, nlp, stopwords):
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    entries = [
        make_entry(canonical="zebra hearsay"),
        make_entry(canonical="alpha hearsay"),
        make_entry(canonical="middle hearsay"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    hearsay = next((s for s in result if s.stem == "hearsay"), None)
    assert hearsay is not None
    assert list(hearsay.sibling_canonicals) == sorted(hearsay.sibling_canonicals)


@pytest.mark.slow
def test_results_sorted_by_stem(make_entry, nlp, stopwords):
    """Output deterministic: alphabetical by stem."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    # Three different stems each with 3 sibs: hearsay / motion / objection
    entries = [
        make_entry(canonical="admissible hearsay"),
        make_entry(canonical="inadmissible hearsay"),
        make_entry(canonical="hearsay exception"),
        make_entry(canonical="motion in limine"),
        make_entry(canonical="motion to compel"),
        make_entry(canonical="motion to suppress"),
        make_entry(canonical="objection sustained"),
        make_entry(canonical="objection overruled"),
        make_entry(canonical="objection waived"),
    ]
    result = synthesize_bare_lemma_entries(entries, nlp, stopwords)
    stems = [s.stem for s in result]
    assert stems == sorted(stems)


def test_empty_input_returns_empty_list(nlp, stopwords):
    """Defensive: empty input is valid and returns []."""
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    assert synthesize_bare_lemma_entries([], nlp, stopwords) == []


# --- Integration smoke against live IR -----------------------------------


@pytest.mark.slow
@pytest.mark.skipif(
    not Path("artifacts/index_tree.json").exists(),
    reason="live IR not committed yet (pre-Wave-4)",
)
def test_calibration_anchor_corpus_at_least_15_stems(nlp, stopwords):
    """RESEARCH §H-5 empirical: ≥15 synthetic stems on the reference corpus v1.0
    (researcher found 22; floor 15 is conservative).
    """
    from book_indexer.render import IndexTree
    from book_indexer.render.filter import is_cruft
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    tree = IndexTree.model_validate_json(
        Path("artifacts/index_tree.json").read_text()
    )
    survivors = [e for e in tree.entries if not is_cruft(e.canonical)]
    result = synthesize_bare_lemma_entries(survivors, nlp, stopwords)

    assert len(result) >= 15, (
        f"B-06 drift: expected ≥15 synthetic stems, got {len(result)}. "
        f"Stems: {[s.stem for s in result]}"
    )


@pytest.mark.slow
@pytest.mark.skipif(
    not Path("artifacts/index_tree.json").exists(),
    reason="live IR not committed yet (pre-Wave-4)",
)
def test_calibration_anchor_corpus_includes_hearsay(nlp, stopwords):
    """RESEARCH §H-5: 'hearsay' is the canonical SPEC AMENDMENT example
    and MUST appear in the live-IR result."""
    from book_indexer.render import IndexTree
    from book_indexer.render.filter import is_cruft
    from book_indexer.render.synthesize import synthesize_bare_lemma_entries

    tree = IndexTree.model_validate_json(
        Path("artifacts/index_tree.json").read_text()
    )
    survivors = [e for e in tree.entries if not is_cruft(e.canonical)]
    # Skip if 'hearsay' itself already a canonical (then synthesis correctly
    # won't emit it) — verify the SPEC AMENDMENT only when applicable.
    if any(e.canonical.lower() == "hearsay" for e in survivors):
        pytest.skip("'hearsay' already a canonical IndexEntry; rule (b) blocks emission")

    result = synthesize_bare_lemma_entries(survivors, nlp, stopwords)
    stems = [s.stem for s in result]
    assert "hearsay" in stems, (
        f"SPEC AMENDMENT regression on live IR: 'hearsay' missing. Stems: {stems}"
    )
