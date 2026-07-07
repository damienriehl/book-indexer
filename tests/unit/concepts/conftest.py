"""Test fixtures scoped to tests/unit/concepts/.

Phase 3a per-subpackage fixtures. Parent `tests/conftest.py` provides:
- `frozen_env` (autouse) — PYTHONHASHSEED=0, TZ=UTC, LC_ALL=C.UTF-8 per test.
- `_warm_spacy_pipeline` (session-scoped autouse) — spaCy en_core_web_lg warmup.
- `corpus_conn` — read-only reference corpus connection.

These are inherited automatically via pytest's conftest-resolution; we do NOT
redeclare them here.

requirements_addressed: CON-07 (fabricated_bad_responses feeds
tests/invariants/test_concept_schema_rejects_locators.py in Plan 03A-02);
CON-03 / CON-04 / CON-07 (nlp + nlp_with_doctrinal + corpus_conn fixtures
shared across symbolic + invariant tests added by Plan v2-02)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import spacy
from spacy.language import Language


@pytest.fixture
def fabricated_bad_responses() -> list[tuple[str, dict]]:
    """Six D-19 rejection shapes, parametrized by a short name.

    The 6 shapes target CON-07's expanded attack surface:
    1. extra field ``page`` (int)
    2. extra field ``folio`` (str)
    3. extra field ``section_ref`` (str)
    4. ``example_quote`` starts with the § glyph (D-05 field-wide regex)
    5. ``variants`` contains an ``N.NN`` string (D-05 field-wide regex, list-item level — H-3)
    6. extra field ``pp`` (list[int])

    Every shape must be rejected by ``ConceptDiscoveryResponse.model_validate``
    at the Pydantic layer (the ship-blocker). CLI-layer rejection is a bonus
    and is audited by ``scripts/spike_claude_schema_strictness.py`` (Plan 03A-05).
    """
    base_candidate = {
        "term": "hearsay",
        "canonical_form": "hearsay",
        "variants": [],
        "example_quote": "An out-of-court statement offered for its truth.",
    }
    base_envelope = {
        "schema_version": "1",
        "pass_type": "doctrinal",
        "chunk_id": "ch1",
    }

    def env(**cand_overrides: object) -> dict:
        cand = {**base_candidate, **cand_overrides}
        return {**base_envelope, "candidates": [cand]}

    return [
        ("page_int",          env(page=87)),
        ("folio_str",         env(folio="xii")),
        ("section_ref_str",   env(section_ref="§2.04")),
        ("quote_starts_with_glyph",
                              env(example_quote="§ 2.04 governs relevance of character evidence")),
        ("variants_item_NdotNN",
                              env(variants=["1.05"])),
        ("pp_list_int",       env(pp=[1, 2])),
    ]


# ===========================================================================
# Phase 3a v2 fixtures (added by Plan v2-02) — shared across symbolic +
# invariant tests. Session-scoped to amortize spaCy + corpus load costs.
# ===========================================================================


@pytest.fixture(scope="session")
def nlp() -> Language:
    """Session-scoped spaCy en_core_web_lg — amortize ~3-6s cold load.

    Returns a vanilla pipeline (no EntityRuler). Tests that need the
    doctrinal ruler should depend on ``nlp_with_doctrinal`` instead.
    NOTE: ``nlp_with_doctrinal`` MUTATES this same object by adding the
    ruler — once that fixture has been requested in a session, the
    ``nlp`` fixture is no longer ruler-free. Tests that need a clean
    nlp afterwards should call ``spacy.load("en_core_web_lg")`` directly.
    """
    return spacy.load("en_core_web_lg")


@pytest.fixture(scope="session")
def nlp_with_doctrinal(nlp: Language) -> Language:
    """``nlp`` with EntityRuler(before='ner') populated from
    ``fixtures/doctrinal_patterns.yaml``.

    Returns the SAME ``nlp`` object after adding the ruler (idempotent —
    if the ruler is already in the pipeline, ``build_doctrinal_nlp``
    returns ``nlp`` unchanged).

    IMPORTANT: this fixture mutates the session-scoped ``nlp``. If a test
    needs a clean nlp afterwards, use ``spacy.load("en_core_web_lg")``
    directly within the test.
    """
    from book_indexer.concepts.symbolic import build_doctrinal_nlp
    return build_doctrinal_nlp(nlp)


@pytest.fixture(scope="session")
def corpus_conn() -> sqlite3.Connection:
    """Session-scoped read-only corpus connection.

    Skips the test if ``artifacts/page_corpus.sqlite`` is absent (Phase 1
    not yet run). Mirrors the existing ``open_read_only_corpus`` PRAGMA
    setup (PRAGMA query_only = 1) used in Plan 03A-03's chunker.
    """
    from book_indexer.concepts.chunker import open_read_only_corpus
    repo_root = Path(__file__).resolve().parents[3]
    path = repo_root / "artifacts" / "page_corpus.sqlite"
    if not path.exists():
        pytest.skip(f"corpus not built: {path}")
    return open_read_only_corpus(path)
