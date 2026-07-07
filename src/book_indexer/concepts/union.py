"""Provenance-preserving union merger — D-12 step 3, D-13.

Merges ``list[CallResult]`` into ``dict[canonical_form_key, PoolEntry]``
where ``canonical_form_key = lemmatize(cf.lower().strip())`` (consistent with
Phase 2's lemmatizer via spaCy's shared ``nlp.vocab``). Every ``PoolEntry``
records which passes contributed (``passes`` tuple) and ALL raw candidates
(``candidates`` tuple) so Phase 4 canonicalization can pick winning forms.

PASS_ORDER for v2 is ``("noun_phrase", "doctrinal", "ner")`` — the v1
``"implicit"`` LLM pass was dropped per CONTEXT D-22. The merge mechanic
itself is unchanged; only the enum shrinks.

H-9: ``PoolEntry.passes`` is a ``tuple[str, ...]`` (sorted via
``PASS_ORDER.index(...)``), NEVER a ``set[str]`` crossing a function
boundary. Sorting is by literal pass order, NOT alphabetical — consistent
with ``passes.py``.

requirements_addressed: CON-04, CON-05
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .passes import PASS_ORDER, CallResult
from .schema import ConceptCandidate

if TYPE_CHECKING:
    import spacy.language  # noqa: F401


__all__ = [
    "PoolEntry",
    "canonical_form_key",
    "union_candidates",
]


@dataclass(frozen=True, slots=True)
class PoolEntry:
    """One canonical-form bucket in the union pool.

    ``passes`` is a tuple sorted by ``PASS_ORDER.index(...)`` (H-9
    sorted-at-boundary).
    ``candidates`` preserves all raw ``ConceptCandidate`` records for audit
    (D-13).
    """

    canonical_form_key: str
    passes: tuple[str, ...]
    candidates: tuple[ConceptCandidate, ...]


def canonical_form_key(
    canonical_form: str,
    nlp: spacy.language.Language | None = None,
) -> str:
    """Lowercase, strip, then lemmatize via spaCy if available.

    When ``nlp`` is None we fall back to the lowercased-and-stripped form.
    This allows unit tests to run without loading ``en_core_web_lg``; Plan
    03A-08's CLI entry point always passes a real spaCy pipeline so the
    production union collapses ``voir dires`` and ``voir dire`` to the same
    key.

    B-10 short-circuit (Plan 06-02 architectural finding): when the
    lowercased input matches a phrase-override key in
    ``nlp.meta["_legal_phrase_overrides"]`` (curated in
    ``config/legal_lemma_overrides.yaml``), bypass ``nlp(base)`` and return
    the override's lemma directly. The default spaCy pipeline tokenizes
    hyphenated forms like ``cross-examination`` into 3 sub-tokens
    (``cross``, ``-``, ``examination``) and lemmatizes them
    component-wise, defeating the Phase 1 phrase merger's single-token
    output. CONTEXT 06 D-02; Plan 06-02 cross-callsite patch (3 sites).
    """
    base = canonical_form.lower().strip()
    if nlp is None:
        return base
    # B-10 cross-callsite patch (CONTEXT 06 D-02; Plan 06-02):
    #   (a) whole-string override → return its lemma directly.
    #   (b) compound carve-out → carve hyphenated override substrings out
    #       of `base`, lemmatize residuals separately, stitch with the
    #       override lemma. Mirrors assembly/dedup.py:lemma_bucket_key
    #       so concept canonicals and assembly bucket keys agree on
    #       hyphenated forms (cross-examination, post-trial, etc.).
    phrase_overrides = nlp.meta.get("_legal_phrase_overrides") or {}
    if phrase_overrides and base in phrase_overrides:
        return phrase_overrides[base].lower()
    if phrase_overrides:
        hyphenated_keys = sorted(
            (k for k in phrase_overrides.keys() if "-" in k),
            key=len,
            reverse=True,
        )
        for key in hyphenated_keys:
            if key in base:
                idx = base.index(key)
                left = base[:idx].strip()
                right = base[idx + len(key):].strip()
                lemma_mid = phrase_overrides[key].lower()
                left_key = canonical_form_key(left, nlp) if left else ""
                right_key = canonical_form_key(right, nlp) if right else ""
                parts = [p for p in (left_key, lemma_mid, right_key) if p]
                return " ".join(parts)
    doc = nlp(base)
    # Phase 8 / D-04 cascade (a): apply curated token-level lemma overrides
    # so spaCy verb-lemma collapses (e.g., 'dying' → 'die') don't corrupt
    # the canonical_form_key used by symbolic concept aggregation. Mirrors
    # the matching post-pass in assembly/dedup.py::lemma_bucket_key (added
    # in sub-commit 08-W3-B); without this site, concept canonicals
    # disagree with assembly bucket keys for the 4 verb-gerund overrides.
    token_overrides = nlp.meta.get("_legal_token_overrides") or {}
    lemmas: list[str] = []
    for tok in doc:
        lemma = tok.lemma_
        if not lemma.strip():
            continue
        text_lower = tok.text.lower()
        if text_lower in token_overrides:
            lemma = token_overrides[text_lower]
        lemmas.append(lemma)
    return " ".join(lemmas) if lemmas else base


def union_candidates(
    results: list[CallResult],
    nlp: spacy.language.Language | None = None,
) -> dict[str, PoolEntry]:
    """Merge per-pass results into the canonical-form pool.

    Assumes ``results`` is already D-12-sorted (as returned by
    ``run_all_passes``). We do NOT re-sort here — preserving the caller's
    ordering is part of the determinism contract.
    """
    # Accumulator uses transient sets + lists; converted to immutable tuples
    # at return time (H-9 sorted-at-boundary).
    acc_passes: dict[str, set[str]] = {}
    acc_candidates: dict[str, list[ConceptCandidate]] = {}

    for result in results:
        if result.response is None:
            continue
        for cand in result.response.candidates:
            key = canonical_form_key(cand.canonical_form, nlp)
            acc_passes.setdefault(key, set()).add(result.pass_type)
            acc_candidates.setdefault(key, []).append(cand)

    pool: dict[str, PoolEntry] = {}
    # Iterate keys in sorted order so dict iteration is deterministic
    # (defensive; CPython 3.12 dicts are insertion-ordered, and insertion
    # order was already deterministic via D-12's pre-sorted results).
    for key in sorted(acc_passes.keys()):
        passes_tuple = tuple(sorted(acc_passes[key], key=PASS_ORDER.index))
        cands_tuple = tuple(acc_candidates[key])
        pool[key] = PoolEntry(
            canonical_form_key=key,
            passes=passes_tuple,
            candidates=cands_tuple,
        )
    return pool
