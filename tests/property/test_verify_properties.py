"""requirements_addressed: VER-05, CONTEXT D-07, D-08

Hypothesis property-based tests for verify() against synthetic in-memory
corpora. Fast per-example runs (500 examples per property, deadline 500ms).

Six properties:
  1. snippet_contains_matched_variant         — VER-05(a)
  2. verify_is_deterministic                  — VER-05(b), D-04 list-equality
  3. no_emit_outside_section_token_range      — VER-05(c)
  4. match_mode_variant_consistency           — CONTEXT D-07
  5. section_path_consistency                 — CONTEXT D-08
  6. known_absent_terms_return_empty          — negative path (D-02 curated)
"""
from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from book_indexer.verify import verify
from book_indexer.verify.query_tokenizer import tokenize_query

from .conftest import KNOWN_ABSENT_TERMS, LEGAL_PHRASE_VOCAB

pytestmark = [pytest.mark.property, pytest.mark.hypothesis]


_SETTINGS = settings(
    max_examples=500,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# VER-05(a): snippet contains matched_variant
# ---------------------------------------------------------------------------


@given(term=st.sampled_from(LEGAL_PHRASE_VOCAB))
@_SETTINGS
def test_snippet_contains_matched_variant(term: str, make_simple_phrase_corpus) -> None:
    """VER-05(a): every emitted Evidence.verbatim_snippet contains
    Evidence.matched_variant (case-insensitive).

    The snippet is built from ``tokens.text`` around the match position
    on the same page; ``matched_variant`` is a space-joined slice of the
    same ``tokens.text`` column, so substring containment is the minimum
    invariant for snippet-matched_variant coherence.
    """
    conn, _sid, _sp = make_simple_phrase_corpus(term)
    evidence_list = list(verify(term, conn))
    for ev in evidence_list:
        assert ev.matched_variant.lower() in ev.verbatim_snippet.lower(), (
            f"snippet does not contain matched_variant\n"
            f"  matched_variant={ev.matched_variant!r}\n"
            f"  snippet={ev.verbatim_snippet!r}"
        )


# ---------------------------------------------------------------------------
# VER-05(b): determinism — list(verify(t,c)) == list(verify(t,c))
# ---------------------------------------------------------------------------


@given(term=st.sampled_from(LEGAL_PHRASE_VOCAB))
@_SETTINGS
def test_verify_is_deterministic(term: str, make_simple_phrase_corpus) -> None:
    """VER-05(b) + D-04 contract: iterator order is stable across repeated
    calls — ordered list equality, not set equality. Any drift surfaces as
    a list-diff failure with shrink-friendly reporting."""
    conn, _sid, _sp = make_simple_phrase_corpus(term)
    a = list(verify(term, conn))
    b = list(verify(term, conn))
    assert a == b, (
        f"verify() is not deterministic for term={term!r}\n"
        f"  first  call: {len(a)} Evidence, "
        f"ordering key={[(ev.pdf_page, ev.token_offset) for ev in a]}\n"
        f"  second call: {len(b)} Evidence, "
        f"ordering key={[(ev.pdf_page, ev.token_offset) for ev in b]}"
    )


# ---------------------------------------------------------------------------
# VER-05(c): no Evidence outside a section's token range
# ---------------------------------------------------------------------------


@given(term=st.sampled_from(LEGAL_PHRASE_VOCAB))
@_SETTINGS
def test_no_emit_outside_section(term: str, make_simple_phrase_corpus) -> None:
    """VER-05(c): every Evidence's (pdf_page, token_offset) points at a
    token whose ``section_id`` is non-NULL. Pre-§-1 tokens (front-matter
    prose, blanks) carry ``section_id=NULL`` per Phase 1 D-24 and must
    never become Evidence."""
    conn, _deepest_sid, _sp = make_simple_phrase_corpus(term)
    for ev in verify(term, conn):
        row = conn.execute(
            "SELECT section_id FROM tokens "
            "WHERE pdf_page=? AND token_index=?",
            (ev.pdf_page, ev.token_offset),
        ).fetchone()
        assert row is not None and row[0] is not None, (
            f"Evidence emitted at (pdf_page={ev.pdf_page}, "
            f"token_offset={ev.token_offset}) points at a token with "
            f"section_id=NULL — VER-05(c) violation"
        )


# ---------------------------------------------------------------------------
# D-07: match_mode <-> matched_variant consistency
# ---------------------------------------------------------------------------


@given(term=st.sampled_from(LEGAL_PHRASE_VOCAB))
@_SETTINGS
def test_match_mode_variant_consistency(term: str, make_simple_phrase_corpus) -> None:
    """CONTEXT D-07: for every Evidence, the match_mode is consistent with
    the relationship between ``matched_variant`` and ``canonical_term``
    AT THE TOKEN-SEQUENCE LEVEL (the same level the matcher operates on):

      - exact:   [q.norm for q in tokenize_query(matched_variant)]
                 ==
                 [q.norm for q in tokenize_query(canonical_term)]

      - lemma:   lemma-sequences equal AND norm-sequences differ
                 (otherwise exact would have won — matcher precedence)

      - acronym: (outside this synthetic fixture's scope — no acronym
                  variants are inserted into the corpus)

    NB: whole-string ``normalize()`` equality is NOT the right invariant.
    spaCy tokenizes ``"cross-examination"`` into three tokens
    (``cross`` / ``-`` / ``examination``); the matcher joins ``tokens.text``
    with spaces for ``matched_variant``, yielding ``"cross - examination"``.
    That form has the same TOKEN-NORM SEQUENCE as ``"cross-examination"``
    but a different whole-string normalize — the token-sequence invariant
    is the one the matcher actually enforces.
    """
    conn, _sid, _sp = make_simple_phrase_corpus(term)
    for ev in verify(term, conn):
        norms_v = [q.norm for q in tokenize_query(ev.matched_variant)]
        norms_c = [q.norm for q in tokenize_query(ev.canonical_term)]
        lemmas_v = [q.lemma for q in tokenize_query(ev.matched_variant)]
        lemmas_c = [q.lemma for q in tokenize_query(ev.canonical_term)]

        if ev.match_mode == "exact":
            assert norms_v == norms_c, (
                f"exact mode: token-norm sequence of matched_variant "
                f"{norms_v!r} != canonical_term {norms_c!r}"
            )
        elif ev.match_mode == "lemma":
            assert lemmas_v == lemmas_c, (
                f"lemma mode: token-lemma sequence of matched_variant "
                f"{lemmas_v!r} != canonical_term {lemmas_c!r}"
            )
            assert norms_v != norms_c, (
                f"lemma mode fired but token-norm sequences are equal "
                f"({norms_v!r}) — exact would have won (matcher precedence bug)"
            )
        elif ev.match_mode == "acronym":
            # Synthetic fixture does not inject acronym variants, so this
            # branch should never fire in this test; if it does, the
            # matcher is emitting spurious modes.
            pytest.fail(
                f"acronym mode in synthetic corpus without acronym_variants: {ev}"
            )
        else:
            pytest.fail(f"unknown match_mode: {ev.match_mode!r}")


# ---------------------------------------------------------------------------
# D-08: section_path <-> section_level consistency (+ ancestor-contains)
# ---------------------------------------------------------------------------


@given(term=st.sampled_from(LEGAL_PHRASE_VOCAB))
@_SETTINGS
def test_section_path_consistency(term: str, make_simple_phrase_corpus) -> None:
    """CONTEXT D-08: for every Evidence:
      - len(section_path) == section_level (Pydantic-enforced)
      - section_path[-1] == section_ref  (Pydantic-enforced)
      - section_level is in {1, 2, 3}    (Pydantic-enforced)
      - each ancestor in section_path[:-1] has bounds containing the
        Evidence position. (Not Pydantic-enforced — this is what we
        actually test: the runtime section tree is coherent with the
        resolved path.)

    The first three invariants are redundant with the Pydantic model
    validator but serve as defense in depth against schema regressions.
    """
    conn, _sid, _expected_path = make_simple_phrase_corpus(term)
    for ev in verify(term, conn):
        # Pydantic-level guarantees (would ValidationError before reaching here):
        assert len(ev.section_path) == ev.section_level
        assert ev.section_path[-1] == ev.section_ref
        assert ev.section_level in (1, 2, 3)

        # Ancestor-contains check: each ancestor's bounds contain this
        # evidence's (pdf_page, token_offset).
        for ancestor_ref in ev.section_path[:-1]:
            row = conn.execute(
                "SELECT start_pdf_page, start_token_offset, "
                "       end_pdf_page,   end_token_offset "
                "FROM sections WHERE section_ref = ? LIMIT 1",
                (ancestor_ref,),
            ).fetchone()
            assert row is not None, (
                f"ancestor {ancestor_ref!r} missing from sections"
            )
            spg, stok, epg, etok = row
            pos = (ev.pdf_page, ev.token_offset)
            assert (spg, stok) <= pos <= (epg, etok), (
                f"ancestor {ancestor_ref!r} bounds ({spg},{stok})-({epg},{etok}) "
                f"do not contain Evidence pos {pos}"
            )


# ---------------------------------------------------------------------------
# Negative path: known-absent terms return an empty iterator (D-02 curated)
# ---------------------------------------------------------------------------


@given(term=st.sampled_from(KNOWN_ABSENT_TERMS))
@settings(
    max_examples=50,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_known_absent_terms_return_empty(term: str, make_simple_phrase_corpus) -> None:
    """Negative path: curated known-absent terms yield no Evidence.

    D-02 rationale: using a curated list instead of ``text() + assume()``
    avoids Hypothesis ``filter_too_much`` health check on sparse vocabs.
    """
    conn, _sid, _sp = make_simple_phrase_corpus("voir dire")  # any corpus is fine
    assert list(verify(term, conn)) == [], (
        f"known-absent term {term!r} yielded non-empty Evidence — false positive"
    )
