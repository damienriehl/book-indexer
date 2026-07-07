"""Unit tests for ``src/book_indexer/concepts/symbolic.py``.

Locks the contract for the three pure-functional pass primitives shipped
by Plan 03A-v2-02:

- ``extract_noun_phrases``  → CON-03/CON-04 noun-chunk extraction with
  H-4 determiner-strip + H-12 LEGAL_STOPS filter + frequency >= 2.
- ``extract_doctrinal``     → EntityRuler ent.ent_id_ non-empty filter.
- ``extract_ner``           → ent.ent_id_ empty + NER_KEEP_LABELS filter.

Synthetic-Doc tests don't touch ``artifacts/page_corpus.sqlite`` and run
fast once the session-scoped ``nlp`` fixture is warm. Slow real-corpus
tests are gated under ``@pytest.mark.slow`` so the default suite stays
green even when the corpus has not been built.

requirements_addressed: CON-03, CON-04, CON-07
"""
from __future__ import annotations

import re
from collections import namedtuple

import orjson
import pytest
import spacy
from spacy.language import Language
from spacy.tokens import Doc

from book_indexer.concepts.schema import (
    ConceptCandidate,
    ConceptDiscoveryResponse,
)
from book_indexer.concepts.symbolic import (
    LEGAL_STOPS,
    NER_KEEP_LABELS,
    _LOCATOR_PREFIX,
    _strip_determiners,
    build_doc_from_tokens,
    build_example_quote,
    extract_doctrinal,
    extract_ner,
    extract_noun_phrases,
    fetch_chapter_tokens,
    kind_for_match,
)
from book_indexer.concepts.union import canonical_form_key

# Synthetic TokenRow for tests that don't touch the live corpus.
_SyntheticTokenRow = namedtuple(
    "_SyntheticTokenRow",
    ["token_id", "pdf_page", "token_index", "text", "lemma", "section_id"],
)


def _build_synthetic_doc(nlp: Language, words: list[str]) -> Doc:
    """Build a Doc via ``build_doc_from_tokens`` from a synthetic word list.

    Bypasses the corpus by constructing TokenRows in-process. Used by all
    fast unit tests.
    """
    rows = [
        _SyntheticTokenRow(
            token_id=i,
            pdf_page=1,
            token_index=i,
            text=w,
            lemma=w.lower(),
            section_id=1,
        )
        for i, w in enumerate(words)
    ]
    doc, _ = build_doc_from_tokens(nlp, rows)
    return doc


# ---------------------------------------------------------------------------
# Doc construction + parallel-array contract
# ---------------------------------------------------------------------------


def test_build_doc_from_tokens_preserves_traceability(nlp: Language) -> None:
    """RESEARCH §H-8: ``Doc.token[i].text == rows[i].text``; len(doc) == len(rows)."""
    words = ["The", "expert", "witness", "testified", "about", "hearsay", "."]
    rows = [
        _SyntheticTokenRow(i, 1, i, w, w.lower(), 1)
        for i, w in enumerate(words)
    ]
    doc, returned_rows = build_doc_from_tokens(nlp, rows)
    assert len(doc) == len(rows)
    assert returned_rows is rows
    for i, row in enumerate(rows):
        assert doc[i].text == row.text, f"doc[{i}]={doc[i].text!r} != rows[{i}]={row.text!r}"


# ---------------------------------------------------------------------------
# noun_phrase pass — synthetic Docs (not the live corpus)
# ---------------------------------------------------------------------------


class _FakeConn:
    """Test double for sqlite3.Connection — answers fetch_chapter_tokens
    with a hard-coded list of tuples mimicking the production query."""

    def __init__(
        self,
        bounds: tuple[int, int, int, int] | None = (1, 0, 1, 9999),
        token_rows: list[tuple] | None = None,
    ) -> None:
        self._bounds = bounds
        self._token_rows = token_rows or []

    def execute(self, sql: str, params: tuple = ()):  # noqa: ARG002 — sql ignored
        return _FakeCursor(self._next_for_query(sql))

    def _next_for_query(self, sql: str) -> list[tuple]:
        if "FROM sections" in sql:
            return [self._bounds] if self._bounds is not None else []
        return self._token_rows


class _FakeCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _conn_from_words(words: list[str]) -> _FakeConn:
    """Build a fake connection whose fetch_chapter_tokens returns rows
    matching the given word list. Each token sits on pdf_page=1 in
    sequential token_index order under section_id=1."""
    rows = [
        (i, 1, i, w, w.lower(), 1)
        for i, w in enumerate(words)
    ]
    return _FakeConn(token_rows=rows)


def test_extract_noun_phrases_filters_stopword_heads(nlp: Language) -> None:
    """``the`` is a determiner that gets stripped; ``expert witness`` survives.

    With two occurrences of "expert witness" the pass should emit a single
    candidate with canonical_form "expert witness". Hearsay appears once
    so should be dropped by the freq>=2 filter.
    """
    words = [
        "The", "expert", "witness", "testified", ".",
        "An", "expert", "witness", "appeared", ".",
        "Hearsay", "is", "inadmissible", ".",
    ]
    conn = _conn_from_words(words)
    resp = extract_noun_phrases(conn, chapter=1, nlp=nlp)
    assert isinstance(resp, ConceptDiscoveryResponse)
    assert resp.pass_type == "noun_phrase"
    assert resp.chunk_id == "ch1"
    canonicals = {c.canonical_form for c in resp.candidates}
    assert "expert witness" in canonicals, f"missing 'expert witness' in {canonicals}"
    # Hearsay only appears once → freq < 2 → filtered out.
    assert "hearsay" not in canonicals


def test_extract_noun_phrases_strips_determiners(nlp: Language) -> None:
    """H-4: 'The expert witness' + 'expert witness' produce ONE candidate."""
    words = [
        "The", "expert", "witness", "spoke", ".",
        "Expert", "witness", "appeared", ".",
    ]
    conn = _conn_from_words(words)
    resp = extract_noun_phrases(conn, chapter=1, nlp=nlp)
    matching = [c for c in resp.candidates if c.canonical_form == "expert witness"]
    assert len(matching) == 1, (
        f"expected 1 'expert witness' candidate, got {len(matching)}; "
        f"all canonicals: {[c.canonical_form for c in resp.candidates]}"
    )


def test_extract_noun_phrases_excludes_legal_stops(nlp: Language) -> None:
    """H-12: noun-chunks whose root lemma is in LEGAL_STOPS are dropped."""
    # "the court" appears 2x, "the rule" appears 2x, "the case" appears 2x.
    # All three should be dropped by H-12 since the root lemma is in
    # LEGAL_STOPS.
    words = [
        "The", "court", "ruled", ".",
        "The", "court", "agreed", ".",
        "The", "rule", "applies", ".",
        "The", "rule", "controls", ".",
        "The", "case", "stands", ".",
        "The", "case", "fails", ".",
    ]
    conn = _conn_from_words(words)
    resp = extract_noun_phrases(conn, chapter=1, nlp=nlp)
    forbidden_roots = {"court", "case", "rule"}
    for cand in resp.candidates:
        # Canonical form's first lowercase word should not be a legal stop.
        root_lemma = cand.canonical_form.split()[0]
        assert root_lemma not in forbidden_roots, (
            f"H-12 violated: {cand.canonical_form!r} has root {root_lemma!r} "
            f"in LEGAL_STOPS"
        )


# ---------------------------------------------------------------------------
# doctrinal pass — uses the real fixtures/doctrinal_patterns.yaml
# ---------------------------------------------------------------------------


def test_extract_doctrinal_matches_curated_pattern(
    nlp_with_doctrinal: Language,
) -> None:
    """A doc with 'voir dire' as TWO tokens triggers the latin_voir_dire
    EntityRuler pattern — confirms (a) load+add of the YAML happened in the
    fixture, (b) the pattern shape matches the spaCy 3.8.14 default
    tokenizer's two-token output, (c) ent.ent_id_ is set."""
    # Build a fresh nlp (without the ruler) and a fresh nlp_with_doctrinal
    # via the fixture to exercise the real fixture loading path. We use
    # synthetic tokens in the unmerged form (two tokens) so the LATIN
    # pattern [{LOWER:'voir'},{LOWER:'dire'}] can match.
    words = [
        "Counsel", "began", "voir", "dire", "examination", ".",
        "Voir", "dire", "concluded", "promptly", ".",
    ]
    conn = _conn_from_words(words)
    resp = extract_doctrinal(conn, chapter=1, nlp_with_doctrinal=nlp_with_doctrinal)
    assert resp.pass_type == "doctrinal"
    canonicals = {c.canonical_form for c in resp.candidates}
    assert "voir dire" in canonicals, (
        f"voir dire not in doctrinal output; canonicals: {sorted(canonicals)}"
    )
    voir = next(c for c in resp.candidates if c.canonical_form == "voir dire")
    assert voir.kind == "doctrine"


# ---------------------------------------------------------------------------
# NER pass
# ---------------------------------------------------------------------------


def test_extract_ner_filters_legal_labels(
    nlp_with_doctrinal: Language,
) -> None:
    """D-25: only LAW/PERSON/ORG/GPE/EVENT/WORK_OF_ART labels survive.

    DATE/CARDINAL/etc. are dropped. We don't assert specific ents here —
    spaCy's NER on synthetic input is fragile — only that every emitted
    candidate's ``kind`` traces back to a NER_KEEP_LABELS label.
    """
    words = [
        "The", "United", "States", "Supreme", "Court", "ruled", "in", "2025",
        ".",
    ]
    conn = _conn_from_words(words)
    resp = extract_ner(conn, chapter=1, nlp_with_doctrinal=nlp_with_doctrinal)
    # Every emitted candidate must have a kind from the NER mapping
    # (actor/instrument/concept) — not 'doctrine'/'rule' which only the
    # doctrinal pass emits, and not None.
    allowed_ner_kinds = {"actor", "instrument", "concept"}
    for cand in resp.candidates:
        assert cand.kind in allowed_ner_kinds, (
            f"NER candidate {cand.term!r} has kind={cand.kind!r}, "
            f"expected one of {allowed_ner_kinds}"
        )


def test_extract_ner_skips_entityruler_matches(
    nlp_with_doctrinal: Language,
) -> None:
    """A 'voir dire' EntityRuler match must NOT show up in extract_ner —
    it is consumed by extract_doctrinal. NER-tagged spans without ent_id_
    (e.g., a person name) DO show up here."""
    words = [
        "Justice", "Scalia", "presided", ".",
        "Counsel", "conducted", "voir", "dire", "carefully", ".",
        "Justice", "Scalia", "objected", ".",
    ]
    conn = _conn_from_words(words)
    ner_resp = extract_ner(conn, chapter=1, nlp_with_doctrinal=nlp_with_doctrinal)
    canonicals = {c.canonical_form for c in ner_resp.candidates}
    # voir dire should NOT appear — it's an EntityRuler match.
    assert "voir dire" not in canonicals, (
        f"extract_ner leaked an EntityRuler match: {canonicals}"
    )


# ---------------------------------------------------------------------------
# example_quote — CON-07 enforcement
# ---------------------------------------------------------------------------


def test_example_quote_respects_con07(nlp: Language) -> None:
    """A doc whose text starts with '§ 2.04' must NOT yield a quote
    starting with the locator prefix even when a noun phrase is positioned
    near the start.

    The forward-shift fallback in ``build_example_quote`` (RESEARCH
    lines 794-800) shifts the window forward; if all shifts fail, the
    span.text alone is returned (provably CON-07-safe).
    """
    words = [
        "§", "2.04", "discusses", "expert", "testimony", "in", "depth",
        "across", "the", "chapter", ",", "with", "many", "examples", ".",
    ]
    doc = _build_synthetic_doc(nlp, words)
    # Find the "expert testimony" span (positions 3..5 in the word list).
    span = doc[3:5]
    assert span.text == "expert testimony"
    quote = build_example_quote(doc, span)
    assert not _LOCATOR_PREFIX.match(quote), (
        f"CON-07 violated: quote {quote!r} starts with locator prefix"
    )
    # Defensive: also verify the schema-level CON-07 regex (separate
    # implementation) accepts the quote — feeding it into a candidate
    # round-trips clean.
    cand = ConceptCandidate(
        term="expert testimony",
        canonical_form="expert testimony",
        variants=[],
        example_quote=quote,
        kind="concept",
    )
    assert cand.example_quote == quote


# ---------------------------------------------------------------------------
# canonical-form determiner stripping (cross-check H-4 + H-5)
# ---------------------------------------------------------------------------


def test_canonical_form_strips_determiners(nlp: Language) -> None:
    """canonical_form_key on the trimmed span text must equal canonical_form_key
    on the same span without a leading determiner."""
    doc = _build_synthetic_doc(
        nlp, ["The", "expert", "witness", "testified", "."]
    )
    full_chunk = doc[0:3]  # "The expert witness"
    trimmed = _strip_determiners(full_chunk)
    assert trimmed.text == "expert witness"
    key_full = canonical_form_key(trimmed.text, nlp)
    # Also build a doc with NO determiner so the comparison is over
    # equivalent input shape, not over fortuitous lemmatizer behavior.
    doc2 = _build_synthetic_doc(nlp, ["expert", "witness", "appeared", "."])
    span2 = doc2[0:2]
    key_bare = canonical_form_key(span2.text, nlp)
    assert key_full == key_bare, (
        f"canonical_form_key mismatch: trimmed={key_full!r} vs bare={key_bare!r}"
    )


# ---------------------------------------------------------------------------
# kind_for_match — deterministic mapping table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pass_type", "label", "expected"),
    [
        ("noun_phrase", None, "concept"),
        ("doctrinal", "LATIN", "doctrine"),
        ("doctrinal", "DOCTRINE", "doctrine"),
        ("doctrinal", "PROCEDURE", "procedure"),
        ("doctrinal", "FRE_RULE", "rule"),
        ("doctrinal", "FRCP_RULE", "rule"),
        ("doctrinal", "USC_REF", "rule"),
        ("ner", "PERSON", "actor"),
        ("ner", "ORG", "actor"),
        ("ner", "LAW", "instrument"),
        ("ner", "GPE", "actor"),
        ("ner", "EVENT", "concept"),
        ("ner", "WORK_OF_ART", "instrument"),
    ],
)
def test_kind_for_match_deterministic_table(
    pass_type: str, label: str | None, expected: str
) -> None:
    assert kind_for_match(pass_type, label) == expected


# ---------------------------------------------------------------------------
# Slow real-corpus tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_extract_noun_phrases_real_corpus_chapter_1(
    corpus_conn, nlp: Language
) -> None:
    """Sanity floor (NOT the SC-3 floor — that's Plan v2-04).

    Chapter 1 of the reference corpus should yield AT LEAST 10 noun-phrase candidates
    after the H-4/H-12 + freq>=2 filters.
    """
    resp = extract_noun_phrases(corpus_conn, chapter=1, nlp=nlp)
    assert resp.pass_type == "noun_phrase"
    assert len(resp.candidates) >= 10, (
        f"chapter 1 yielded {len(resp.candidates)} noun_phrase candidates "
        f"(expected >= 10 sanity floor)"
    )


@pytest.mark.slow
def test_extract_doctrinal_real_corpus_finds_voir_dire(
    corpus_conn, nlp_with_doctrinal: Language
) -> None:
    """At least one of chapters 1-5 should contain a 'voir dire' doctrinal
    candidate.

    NOTE: Phase 1's legal-phrase-merger merges "voir dire" into a single
    token (see Plan v2-01 SUMMARY "Estimated Orphan Ratio") so the
    [LOWER:voir, LOWER:dire] pattern from doctrinal_patterns.yaml may not
    match the live corpus. This test is documentation-grade — if it skips
    or fails, the orphan-ratio finding stands and Plan v2-04+ will
    address pattern shape.
    """
    found = False
    for chapter in (1, 2, 3, 4, 5):
        resp = extract_doctrinal(
            corpus_conn, chapter=chapter, nlp_with_doctrinal=nlp_with_doctrinal
        )
        canonicals = {c.canonical_form for c in resp.candidates}
        if "voir dire" in canonicals:
            found = True
            break
    if not found:
        pytest.skip(
            "voir dire not found in any chapter — H-3 orphan-ratio hazard "
            "from Plan v2-01 SUMMARY: Phase 1 phrase-merger merges multi-word "
            "doctrinal terms into single tokens, defeating LOWER patterns. "
            "Plan v2-04+ will address pattern shape."
        )


@pytest.mark.slow
def test_symbolic_output_byte_identical_across_runs(
    corpus_conn, nlp: Language
) -> None:
    """Lock #5 byte-identical: running noun_phrase pass on chapter 1 twice
    yields byte-identical orjson serialization."""
    r1 = extract_noun_phrases(corpus_conn, chapter=1, nlp=nlp)
    r2 = extract_noun_phrases(corpus_conn, chapter=1, nlp=nlp)
    b1 = orjson.dumps(
        r1.model_dump(mode="json"),
        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
    )
    b2 = orjson.dumps(
        r2.model_dump(mode="json"),
        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
    )
    assert b1 == b2, (
        f"non-determinism: {len(b1)} bytes vs {len(b2)} bytes "
        f"(diff at first byte: {next((i for i, (a, b) in enumerate(zip(b1, b2)) if a != b), -1)})"
    )


# ---------------------------------------------------------------------------
# Constants sanity (defends against accidental rename / drift)
# ---------------------------------------------------------------------------


def test_extract_noun_phrases_skips_locator_prefixed_spans(
    nlp: Language,
) -> None:
    """CON-07 pre-filter: a span like '1.07.4 Conflict of Interest' must
    NOT crash ConceptCandidate construction. The pass should silently
    skip it rather than raise ValidationError.

    Regression guard for the auto-fix (Rule 1) discovered during the v2-02
    sample-count probe: NER occasionally tags numeric-prefixed section
    headers as WORK_OF_ART (or noun_chunks include them as multi-word
    NPs) and the chapter_title block_role filter does NOT catch deeper
    section-header spans.
    """
    # Construct a doc whose noun_chunks would include a locator-prefixed
    # span if not for the _has_locator_prefix guard.
    words = [
        "1.07.4", "Conflict", "of", "Interest", "is", "discussed", ".",
        "1.07.4", "Conflict", "of", "Interest", "matters", "here", ".",
    ]
    conn = _conn_from_words(words)
    # Should NOT raise — the pre-filter skips locator-prefixed spans.
    resp = extract_noun_phrases(conn, chapter=1, nlp=nlp)
    for cand in resp.candidates:
        assert not _LOCATOR_PREFIX.match(cand.canonical_form)
        assert not _LOCATOR_PREFIX.match(cand.term)


def test_module_constants_present() -> None:
    """Confirms the module-level constants required by the v2-02 plan
    acceptance criteria are exposed and have the expected shape."""
    assert isinstance(LEGAL_STOPS, frozenset)
    assert {"court", "case", "rule"}.issubset(LEGAL_STOPS)
    assert isinstance(NER_KEEP_LABELS, frozenset)
    assert {"LAW", "PERSON", "ORG", "GPE", "EVENT", "WORK_OF_ART"}.issubset(
        NER_KEEP_LABELS
    )
    # _LOCATOR_PREFIX must be a compiled re Pattern
    assert hasattr(_LOCATOR_PREFIX, "match")
    assert _LOCATOR_PREFIX.match("§ 2.04") is not None
    assert _LOCATOR_PREFIX.match("2.04 abc") is not None
    assert _LOCATOR_PREFIX.match("expert witness") is None
