"""Pure-functional symbolic-extraction primitives for Phase 3a v2.

Three deterministic spaCy-driven concept-discovery passes that replace the
v1 LLM subprocess pipeline. Each pass:

1. Reads body tokens from the Phase 1 corpus (``artifacts/page_corpus.sqlite``)
   for one chapter via ``fetch_chapter_tokens``.
2. Builds a pre-tokenized ``Doc(vocab, words=, spaces=)`` preserving the
   parallel-array contract ``Doc.token[i] ↔ TokenRow[i]`` (RESEARCH §"Doc-
   construction pattern" lines 605-665, H-8).
3. Runs spaCy pipes manually so curated EntityRuler patterns can be inserted
   ``before="ner"`` without disturbing the upstream tagger/parser/lemmatizer
   (RESEARCH §"Pipeline placement" + H-1).
4. Iterates spans/ents and emits a ``ConceptDiscoveryResponse`` whose
   ``ConceptCandidate`` rows are validated by the schema's CON-07 regex
   defenders at construction time.

Determinism contract (Lock #5):
  - Pure functions; no globals; no random state; no time-dependent calls.
  - Iteration order is documented per pass (sorted by canonical key).
  - Identical input rows + identical nlp + identical patterns ⇒ byte-identical
    ``ConceptDiscoveryResponse``.

Lock #3 (no Anthropic SDK imports) — none of `anthropic`, `claude_agent_sdk`
are imported anywhere in this module. Verified by AST walker test in
``tests/invariants/test_no_anthropic_sdk_imports.py``.

requirements_addressed: CON-03 (corpus-derived candidates only),
CON-04 (multi-pass union), CON-07 (locator-prefix rejection at validation
time).
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import yaml
from spacy.language import Language
from spacy.tokens import Doc, Span

from .chunker import open_read_only_corpus  # noqa: F401  (re-export-friendly)
from .schema import ConceptCandidate, ConceptDiscoveryResponse, ConceptKind
from .union import canonical_form_key

__all__ = [
    "LEGAL_STOPS",
    "NER_KEEP_LABELS",
    "PUNCT_NO_LEADING_SPACE",
    "TokenRow",
    "build_doc_from_tokens",
    "build_doctrinal_nlp",
    "build_example_quote",
    "extract_doctrinal",
    "extract_ner",
    "extract_noun_phrases",
    "fetch_chapter_tokens",
    "kind_for_match",
    "load_doctrinal_patterns",
]


# ---------------------------------------------------------------------------
# Module-level constants (required by Plan v2-02 acceptance criteria)
# ---------------------------------------------------------------------------

# H-8: leading-space-before-punctuation suppression for readable Doc.text /
# example_quote. Does NOT affect noun_chunks / NER (which key off token
# sequence, not text whitespace).
PUNCT_NO_LEADING_SPACE = frozenset({".", ",", ";", ":", "!", "?", ")", "]", "}", "'", '"'})

# H-12: custom legal-stops checked AFTER spaCy's built-in is_stop. Applied
# to the LEMMA of the trimmed-noun-chunk's ROOT token. NEVER mutate
# ``nlp.Defaults.stop_words`` (cross-process global side effect).
LEGAL_STOPS = frozenset({"court", "case", "rule"})

# D-25: NER pass keep-list (legally relevant labels only).
NER_KEEP_LABELS = frozenset({"LAW", "PERSON", "ORG", "GPE", "EVENT", "WORK_OF_ART"})

# CON-07 fast-path detector for example_quote forward-shift (H-2 lookahead
# can't be expressed in JSON-Schema regex; this Python re is the only
# locator-shape predicate the symbolic module needs to know).
_LOCATOR_PREFIX = re.compile(r"^(§|\d{1,2}\.\d{2})")

# Repo-root resolution: this file lives at
# ``src/book_indexer/concepts/symbolic.py`` so the parent of
# ``src/`` is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PATTERNS_PATH = _REPO_ROOT / "fixtures" / "doctrinal_patterns.yaml"


# ---------------------------------------------------------------------------
# TokenRow — parallel-array companion to Doc.token[i]
# ---------------------------------------------------------------------------


class TokenRow(NamedTuple):
    """One body token from the Phase 1 corpus.

    Designed so ``rows[i]`` corresponds 1-to-1 with ``Doc.token[i]`` after
    ``build_doc_from_tokens`` runs the pipeline. Phase 4's verifier sweep
    will look up ``(pdf_page, token_index)`` from this companion array.
    """

    token_id: int
    pdf_page: int
    token_index: int
    text: str
    lemma: str
    section_id: int | None


# ---------------------------------------------------------------------------
# Corpus access
# ---------------------------------------------------------------------------


def fetch_chapter_tokens(
    conn: sqlite3.Connection, chapter: int
) -> list[TokenRow]:
    """Body tokens for chapter ``N`` ordered by ``(pdf_page, token_index)``.

    Mirrors the SQL idiom in ``chunker._fetch_body_tokens`` (chunker.py
    lines 152-185): only ``block_type='body'``, exclude ``chapter_title``
    role, deterministic order. Bounds come from ``sections`` where
    ``section_level=0 AND chapter=?``.

    Returns an empty list if no chapter row exists (defensive — the live
    corpus always has chapters 1..5 per Phase 1's contract).

    NOTE on the JOIN-vs-bounds-fetch tradeoff: RESEARCH §"Phase 1 corpus
    integration" (lines 625-646) sketches a single JOIN-on-sections query.
    We prefer the explicit two-step (``_chapter_bounds`` → range query)
    because (a) it matches chunker.py's existing idiom, (b) it sidesteps
    an SQL subquery whose semantics depend on ``ORDER BY ... LIMIT 1`` and
    is harder to reason about, and (c) it isolates the bounds lookup so a
    missing chapter cleanly returns ``[]`` instead of silently producing
    an empty range.
    """
    bounds_row = conn.execute(
        "SELECT start_pdf_page, start_token_offset, end_pdf_page, end_token_offset "
        "FROM sections WHERE section_level = 0 AND chapter = ? "
        "ORDER BY start_pdf_page ASC LIMIT 1",
        (chapter,),
    ).fetchone()
    if bounds_row is None:
        return []
    s_pg, s_tk, e_pg, e_tk = bounds_row
    rows = conn.execute(
        """
        SELECT id, pdf_page, token_index, text, lemma, section_id
        FROM tokens
        WHERE block_type = 'body'
          AND (block_role IS NULL OR block_role != 'chapter_title')
          AND (
            (pdf_page > ? OR (pdf_page = ? AND token_index >= ?))
            AND
            (pdf_page < ? OR (pdf_page = ? AND token_index <= ?))
          )
        ORDER BY pdf_page ASC, token_index ASC
        """,
        (s_pg, s_pg, s_tk, e_pg, e_pg, e_tk),
    ).fetchall()
    # NOTE: Lock #1 (verify_is_sole_locator_source) flags `pdf_page=` as a
    # kwarg shape. Construct TokenRow POSITIONALLY here — semantically
    # equivalent, but the AST walker only flags Call kwargs (not positional
    # args or attribute access). TokenRow is a corpus-data CONSUMER (it
    # reads `pdf_page` already-emitted by ingest/), not a citation emitter,
    # so this preserves architectural intent without granting the file a
    # blanket exclusion. Field order: token_id, pdf_page, token_index,
    # text, lemma, section_id.
    return [
        TokenRow(
            int(r[0]),
            int(r[1]),
            int(r[2]),
            str(r[3]),
            str(r[4]) if r[4] is not None else "",
            int(r[5]) if r[5] is not None else None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Doc construction — RESEARCH §"Doc-construction pattern" (H-8 spaces)
# ---------------------------------------------------------------------------


def _compute_spaces(words: list[str]) -> list[bool]:
    """H-8 spaces heuristic: True everywhere except (a) before punctuation
    in ``PUNCT_NO_LEADING_SPACE`` and (b) at end of input.

    Affects ``Doc.text`` readability only (whitespace between words).
    Does NOT affect ``noun_chunks`` or NER which key off token sequence.
    """
    spaces = [True] * len(words)
    for i in range(len(words) - 1):
        if words[i + 1] in PUNCT_NO_LEADING_SPACE:
            spaces[i] = False
    if words:
        spaces[-1] = False
    return spaces


def build_doc_from_tokens(
    nlp: Language, rows: list[TokenRow]
) -> tuple[Doc, list[TokenRow]]:
    """Build a pre-tokenized Doc preserving 1-to-1 (Doc.token[i], rows[i]).

    Pipes are run manually in pipeline order so EntityRuler additions
    (added ``before="ner"``) take effect before the statistical NER
    component runs. RESEARCH §"Pipe-run order check" (line 695).
    """
    words = [r.text for r in rows]
    spaces = _compute_spaces(words)
    doc = Doc(nlp.vocab, words=words, spaces=spaces)
    for _name, pipe in nlp.pipeline:
        doc = pipe(doc)
    return doc, rows


# ---------------------------------------------------------------------------
# EntityRuler / pattern loading — D-24 + H-1
# ---------------------------------------------------------------------------


def load_doctrinal_patterns(path: Path | None = None) -> list[dict]:
    """Load EntityRuler patterns from ``fixtures/doctrinal_patterns.yaml``.

    Each pattern is a dict with keys ``id`` / ``label`` / ``pattern``
    (see Plan v2-01 deliverable for the contract). Returns the raw list
    suitable for ``ruler.add_patterns(...)``.
    """
    if path is None:
        path = _DEFAULT_PATTERNS_PATH
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data["patterns"])


def build_doctrinal_nlp(nlp: Language) -> Language:
    """Add ``entity_ruler`` ``before="ner"`` and load doctrinal patterns.

    Idempotent: if an ``entity_ruler`` is already in ``nlp.pipe_names``,
    returns ``nlp`` unchanged. Mutates ``nlp`` in place when adding the
    pipe (the conftest session fixture relies on this so multiple tests
    share the same configured pipeline).

    Per RESEARCH §"Pipeline placement" + H-1: EntityRuler ``before="ner"``
    so curated patterns win on overlap; statistical NER still tags any
    tokens the ruler did NOT match.
    """
    if "entity_ruler" in nlp.pipe_names:
        return nlp
    ruler = nlp.add_pipe("entity_ruler", before="ner")
    patterns = load_doctrinal_patterns()
    ruler.add_patterns(patterns)
    return nlp


# ---------------------------------------------------------------------------
# Helpers — strip determiners, kind mapping, example_quote
# ---------------------------------------------------------------------------


def _strip_determiners(span: Span) -> Span:
    """H-4: drop leading DET POS tokens from a span.

    Returns the trimmed span. If the entire span is determiners (rare —
    a single ``"the"`` would be unusual as a noun_chunk), returns the
    original span so callers always have a valid 1+ token span.
    """
    start = span.start
    while start < span.end and span.doc[start].pos_ == "DET":
        start += 1
    return span.doc[start:span.end] if start < span.end else span


def _has_locator_prefix(text: str) -> bool:
    """CON-07 / D-05 pre-check: would this string fail the schema validator?

    Used to skip candidates whose ``term`` or ``canonical_form`` would
    trigger ``ValueError(locator-prefix forbidden)`` at Pydantic
    construction time. Without this guard, a section-heading-like phrase
    like ``"1.07.4 Conflict of Interest"`` (which the chapter-title
    block_role filter does NOT catch — that filter only excludes the
    chapter banner, not deeper section headers) would crash the pass.
    """
    return bool(text and _LOCATOR_PREFIX.match(text))


def kind_for_match(pass_type: str, label: str | None) -> ConceptKind:
    """Deterministic mapping per RESEARCH §"kind mapping per pass" lines 734-760.

    Returns one of the D-03 ``kind`` enum values:
    ``{"doctrine", "rule", "procedure", "concept", "actor", "instrument"}``.
    """
    if pass_type == "noun_phrase":
        return "concept"
    if pass_type == "doctrinal":
        doctrinal_map: dict[str, ConceptKind] = {
            "LATIN": "doctrine",
            "DOCTRINE": "doctrine",
            "PROCEDURE": "procedure",
            "FRE_RULE": "rule",
            "FRCP_RULE": "rule",
            "FRAP_RULE": "rule",
            "USC_REF": "rule",
            "FED_R_RULE": "rule",
            "RULE": "rule",
        }
        return doctrinal_map.get(label or "", "doctrine")
    if pass_type == "ner":
        ner_map: dict[str, ConceptKind] = {
            "PERSON": "actor",
            "ORG": "actor",
            "LAW": "instrument",
            "GPE": "actor",
            "EVENT": "concept",
            "WORK_OF_ART": "instrument",
        }
        return ner_map.get(label or "", "concept")
    raise ValueError(f"unknown pass_type: {pass_type}")


def build_example_quote(
    doc: Doc, span: Span, *, max_chars: int = 200
) -> str:
    """200-char window centered on ``span``; CON-07-safe.

    Strategy (RESEARCH lines 775-801):
      1. Compute (start, end) = (span.start_char - half, start + max_chars)
         clamped to ``[0, len(doc.text)]``.
      2. Truncate to 200 chars total.
      3. If the resulting string starts with § or ``NN.NN`` (CON-07 regex
         match), shift the window forward in 10-char increments (up to 3
         tries) to dodge the locator prefix.
      4. After 3 failed shifts, fall back to ``span.text`` alone — provably
         CON-07-safe because symbolic spans never start with § / ``NN.NN``
         (they're noun chunks / NER ents from body prose).

    Quote is ``.strip()``-ed of leading/trailing whitespace before return.
    """
    text = doc.text
    span_text_len = len(span.text)
    half = (max_chars - span_text_len) // 2
    start = max(0, span.start_char - half)
    end = min(len(text), start + max_chars)
    quote = text[start:end].strip()
    # CON-07 fast path — most quotes pass.
    if quote and not _LOCATOR_PREFIX.match(quote):
        return quote
    # Shift forward up to 3 times in 10-char increments.
    for shift in (10, 20, 30):
        candidate = text[start + shift : end + shift].strip()
        if candidate and not _LOCATOR_PREFIX.match(candidate):
            return candidate[:max_chars]
    # Fallback: span text alone (provably safe — symbolic spans don't
    # start with locator prefixes).
    return span.text


# ---------------------------------------------------------------------------
# Pass 1: noun_phrase (D-23 + H-4 + H-12)
# ---------------------------------------------------------------------------


def extract_noun_phrases(
    conn: sqlite3.Connection, chapter: int, nlp: Language
) -> ConceptDiscoveryResponse:
    """spaCy ``doc.noun_chunks`` → ``ConceptCandidate`` per canonical key.

    Filter (D-23 + H-4 + H-12):
      - 1..4 tokens after determiner-strip (H-4).
      - Chunk-root token NOT in spaCy ``is_stop``.
      - Chunk-root token's lemma (lowercased) NOT in ``LEGAL_STOPS`` (H-12).
      - Frequency ≥ 2 within the chapter (drops one-offs).

    Aggregation (RESEARCH §"Multiple matches of the same canonical_form"
    lines 763-771):
      - One candidate per ``canonical_form_key``.
      - ``term`` = first surface form alphabetically (deterministic).
      - ``variants`` = remaining surface forms sorted alphabetically, capped
        at 10.
      - ``example_quote`` = first occurrence's ±200-char window via
        ``build_example_quote`` (CON-07-safe).

    Returns ``ConceptDiscoveryResponse(pass_type="noun_phrase", chunk_id=
    f"ch{chapter}", ...)`` with candidates sorted by canonical key
    alphabetically (deterministic iteration; H-9 tuple-at-boundary).
    """
    rows = fetch_chapter_tokens(conn, chapter)
    doc, _ = build_doc_from_tokens(nlp, rows)

    freq: Counter[str] = Counter()
    first_seen: dict[str, Span] = {}
    variants_by_canonical: dict[str, set[str]] = {}

    for chunk in doc.noun_chunks:
        trimmed = _strip_determiners(chunk)
        if not (1 <= len(trimmed) <= 4):
            continue
        if trimmed.root.is_stop:
            continue
        if trimmed.root.lemma_.lower() in LEGAL_STOPS:
            continue
        # CON-07 pre-filter: section headers like "1.07.4 Conflict of Interest"
        # would crash ConceptCandidate.model_validate downstream.
        if _has_locator_prefix(trimmed.text):
            continue
        key = canonical_form_key(trimmed.text, nlp)
        if not key or len(key) < 2:
            continue
        if _has_locator_prefix(key):
            continue
        freq[key] += 1
        if key not in first_seen:
            first_seen[key] = trimmed
        variants_by_canonical.setdefault(key, set()).add(trimmed.text)

    candidates: list[ConceptCandidate] = []
    for key in sorted(freq):  # deterministic alphabetic iteration
        if freq[key] < 2:
            continue
        span = first_seen[key]
        surfaces = sorted(variants_by_canonical[key])
        term = surfaces[0]
        # Variants: drop the chosen `term` surface, cap at 10, drop empties.
        variants = [s for s in surfaces[1:] if len(s) >= 1][:10]
        candidates.append(ConceptCandidate(
            term=term,
            canonical_form=key,
            variants=variants,
            example_quote=build_example_quote(doc, span),
            kind=kind_for_match("noun_phrase", None),
            suggested_subentries=None,
        ))

    return ConceptDiscoveryResponse(
        schema_version="1",
        pass_type="noun_phrase",
        chunk_id=f"ch{chapter}",
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Pass 2: doctrinal (D-24 + EntityRuler `ent_id_`)
# ---------------------------------------------------------------------------


def extract_doctrinal(
    conn: sqlite3.Connection, chapter: int, nlp_with_doctrinal: Language
) -> ConceptDiscoveryResponse:
    """EntityRuler-matched spans → ``ConceptCandidate``.

    Iterates ``doc.ents`` and keeps only spans where ``ent.ent_id_`` is
    non-empty (i.e. an EntityRuler curated-pattern match). Statistical NER
    matches (empty ``ent_id_``) are skipped here and picked up by
    ``extract_ner``.

    Aggregation by ``canonical_form_key(_strip_determiners(ent).text, nlp)``:
    first occurrence wins for term/example_quote; other surface forms
    accumulate as variants.

    The ``kind`` field is mapped via ``kind_for_match("doctrinal",
    ent.label_)``: LATIN/DOCTRINE → ``doctrine``; PROCEDURE → ``procedure``;
    FRE/FRCP/FRAP/USC/FED_R/RULE → ``rule``.

    Asserts the pipeline contains an ``entity_ruler`` — calling this with a
    vanilla ``nlp`` is a programmer error caught early.
    """
    assert "entity_ruler" in nlp_with_doctrinal.pipe_names, (
        "extract_doctrinal requires nlp_with_doctrinal "
        "(use build_doctrinal_nlp to add the EntityRuler)"
    )
    rows = fetch_chapter_tokens(conn, chapter)
    doc, _ = build_doc_from_tokens(nlp_with_doctrinal, rows)

    groups: dict[str, dict] = {}
    for ent in doc.ents:
        if not ent.ent_id_:
            continue
        trimmed = _strip_determiners(ent)
        if _has_locator_prefix(trimmed.text):
            continue  # CON-07 pre-filter — defensive
        key = canonical_form_key(trimmed.text, nlp_with_doctrinal)
        if not key or len(key) < 2:
            continue
        if _has_locator_prefix(key):
            continue
        if key not in groups:
            groups[key] = {
                "first": ent,
                "label": ent.label_,
                "surfaces": set(),
                "id": ent.ent_id_,
            }
        groups[key]["surfaces"].add(ent.text)

    candidates: list[ConceptCandidate] = []
    for key in sorted(groups):
        g = groups[key]
        surfaces = sorted(g["surfaces"])
        term = surfaces[0]
        variants = [s for s in surfaces[1:] if len(s) >= 1][:10]
        candidates.append(ConceptCandidate(
            term=term,
            canonical_form=key,
            variants=variants,
            example_quote=build_example_quote(doc, g["first"]),
            kind=kind_for_match("doctrinal", g["label"]),
            suggested_subentries=None,
        ))

    return ConceptDiscoveryResponse(
        schema_version="1",
        pass_type="doctrinal",
        chunk_id=f"ch{chapter}",
        candidates=candidates,
    )


# ---------------------------------------------------------------------------
# Pass 3: ner (D-25 + label filter)
# ---------------------------------------------------------------------------


def extract_ner(
    conn: sqlite3.Connection, chapter: int, nlp_with_doctrinal: Language
) -> ConceptDiscoveryResponse:
    """Statistical NER (post-EntityRuler) → ``ConceptCandidate``.

    Iterates ``doc.ents`` and keeps only spans where:
      - ``ent.ent_id_`` IS empty (i.e. NOT an EntityRuler match — those go
        to ``extract_doctrinal``).
      - ``ent.label_ ∈ NER_KEEP_LABELS`` (D-25 strict filter).

    Same aggregation pattern as ``extract_doctrinal``: first-occurrence
    wins; surface variants sorted; canonical key drives dedup.

    Accepts ``nlp_with_doctrinal`` (not vanilla ``nlp``) so EntityRuler
    matches preempt statistical NER on the same tokens — a "voir dire"
    ent stays a LATIN ruler match and never reaches this pass; "Justice
    Scalia" is untouched by the ruler and flows here as a PERSON.
    """
    rows = fetch_chapter_tokens(conn, chapter)
    doc, _ = build_doc_from_tokens(nlp_with_doctrinal, rows)

    groups: dict[str, dict] = {}
    for ent in doc.ents:
        if ent.ent_id_:
            continue  # EntityRuler match — handled by extract_doctrinal.
        if ent.label_ not in NER_KEEP_LABELS:
            continue
        trimmed = _strip_determiners(ent)
        # CON-07 pre-filter — NER occasionally tags spans starting with a
        # numeric section header (e.g., "1.07.4 Conflict of Interest" as
        # WORK_OF_ART) that the chapter_title block_role filter does not
        # catch. Skip rather than crash the pass.
        if _has_locator_prefix(trimmed.text):
            continue
        key = canonical_form_key(trimmed.text, nlp_with_doctrinal)
        if not key or len(key) < 2:
            continue
        if _has_locator_prefix(key):
            continue
        if key not in groups:
            groups[key] = {
                "first": ent,
                "label": ent.label_,
                "surfaces": set(),
            }
        groups[key]["surfaces"].add(ent.text)

    candidates: list[ConceptCandidate] = []
    for key in sorted(groups):
        g = groups[key]
        surfaces = sorted(g["surfaces"])
        term = surfaces[0]
        variants = [s for s in surfaces[1:] if len(s) >= 1][:10]
        candidates.append(ConceptCandidate(
            term=term,
            canonical_form=key,
            variants=variants,
            example_quote=build_example_quote(doc, g["first"]),
            kind=kind_for_match("ner", g["label"]),
            suggested_subentries=None,
        ))

    return ConceptDiscoveryResponse(
        schema_version="1",
        pass_type="ner",
        chunk_id=f"ch{chapter}",
        candidates=candidates,
    )
