"""B-06 bare-lemma main-entry synthesis (render-time projection).

SPEC AMENDMENT (RESEARCH §H-5): CONTEXT D-04 specified 'first-token lemma
matching' but empirical probe against the live IR proves that approach
MISSES the canonical 'hearsay' example (because 'admissible hearsay'
lemmatizes first-token to 'admissible'). The researcher's empirical
recommendation — and what this module implements — is
UNION-OF-TOKEN-LEMMAS: for each entry's canonical, compute the lemma set
of all alpha tokens (length >= 4); group entries whose lemma sets share a
stem.

Empirical result on the reference corpus (post-B-05 cruft filter, 887 entries):
  22 synthetic candidates including 'hearsay' (4 sibs), 'evidence'
  (29 sibs), 'case', 'jury', 'direct', 'legal', 'improper', 'prior',
  'specific', 'effective', 'favorable', 'evidentiary', 'reasonable',
  'final', 'present', 'privileged', 'common', 'complex', 'story',
  'record', 'accurate'. (RESEARCH §H-5 table.)

Stem rules:
  (a) >=3 sibling canonicals share the stem (token-lemma overlap),
  (b) the bare stem is not already a canonical in `entries`,
  (c) len(stem) >= 4 (filter trivial 1-3 char lemmas),
  (d) stem not in STOPWORDS_EXCLUDE (loaded from
      fixtures/render_stopwords.yaml; per Pitfall §P-7 cross-volume drift,
      companion volumes can override via Phase 6 --stopwords-file).

Output: list[SyntheticEntry] sorted alphabetically by stem. Each
SyntheticEntry's locators are the UNION of sibling locators, deduped by
(section_ref, folio).

The synthetic entries are render-time projections — they have NO row in
Phase 4's IR, and NO row in artifacts/audit/index_evidence.json (which
is per-Locator, not per-Entry, per Open Question 3 — RESEARCH §H-12).
Coverage.md section 13 (per RESEARCH §H-10) records them for audit.

requirements_addressed: implicit D-04 / B-06 (CONTEXT-locked, refined
per RESEARCH §H-5 empirical SPEC AMENDMENT).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from .ir import IndexEntry, Locator, SyntheticEntry

STOPWORDS_PATH: Path = Path("fixtures/render_stopwords.yaml")


def load_stopwords(path: Path = STOPWORDS_PATH) -> set[str]:
    """Read fixtures/render_stopwords.yaml → set of lowercase lemmas.

    Caller passes the result to synthesize_bare_lemma_entries() as the
    `stopwords` argument. Per Pitfall §P-7 the set is corpus-dependent;
    Phase 6 CLI exposes a `--stopwords-file` flag for per-volume overrides.
    """
    data = yaml.safe_load(Path(path).read_text())
    return {row["lemma"].lower() for row in data["stopwords"]}


def _dedupe_locators(siblings: list[IndexEntry]) -> tuple[Locator, ...]:
    """Union sibling locators; dedupe by (section_ref, folio).

    Sort by (section_ref ASC, integer-cast-folio ASC if numeric else
    string ASC) for byte-determinism. evidence_id of the FIRST occurrence
    wins on ties (preserves audit join — RESEARCH §H-12).
    """
    seen: dict[tuple[str, str], Locator] = {}
    for sib in siblings:
        for loc in sib.locators:
            key = (loc.section_ref, loc.folio)
            if key not in seen:
                seen[key] = loc

    def sort_key(loc: Locator) -> tuple[str | int, ...]:
        try:
            return (loc.section_ref, 0, "") + (int(loc.folio),)  # numeric sort
        except (TypeError, ValueError):
            return (loc.section_ref, 1, loc.folio)  # string sort, after numerics

    # Sort numerics first (key tuple length 4) then strings (length 3) — but
    # mixing tuple lengths is messy; use a uniform sort_key instead.
    def uniform_key(loc: Locator) -> tuple[str, int, int, str]:
        try:
            return (loc.section_ref, 0, int(loc.folio), "")
        except (TypeError, ValueError):
            return (loc.section_ref, 1, 0, loc.folio)

    return tuple(sorted(seen.values(), key=uniform_key))


def synthesize_bare_lemma_entries(
    entries: list[IndexEntry],
    nlp: Any,
    stopwords: set[str],
) -> list[SyntheticEntry]:
    """Per RESEARCH §H-5 SPEC AMENDMENT — union-of-token-lemmas synthesis.

    Args:
        entries: post-B-05-filter list of IndexEntry instances.
        nlp: spaCy Language with a tagger+lemmatizer (typically
            en_core_web_lg). NOT loaded here; caller passes the instance.
        stopwords: set of lowercase lemmas to exclude (typically loaded
            from fixtures/render_stopwords.yaml).

    Returns:
        list[SyntheticEntry] sorted alphabetically by stem.

    Per Open Question 3 (RESEARCH §H-12): synthesized entries do NOT appear
    in artifacts/audit/index_evidence.json (which is per-Locator); they
    appear ONLY in artifacts/render/index.{md,docx} and coverage.md
    section 13.
    """
    if not entries:
        return []

    canon_set = {e.canonical.lower() for e in entries}
    groups: dict[str, set[str]] = defaultdict(set)

    # B-10 short-circuit (CONTEXT 06 D-02; Plan 06-02 cross-callsite patch):
    # the default spaCy tokenizer fragments hyphenated legal terms
    # (cross-examination → cross / - / examination), and synthesizing bare
    # lemmas across those fragments would emit a spurious 'cross' or
    # 'examination' stem. When the entry's canonical (lowercased) matches
    # a curated phrase override, treat its lemma as a single atomic token
    # and skip union-of-token-lemmas decomposition.
    phrase_overrides = nlp.meta.get("_legal_phrase_overrides") or {} if nlp is not None else {}

    # ``entries`` is non-empty here (early return above), so the loop always
    # calls ``nlp`` — the caller contract guarantees a loaded Language.
    assert nlp is not None
    for e in entries:
        canonical_lc = e.canonical.lower().strip()
        if phrase_overrides and canonical_lc in phrase_overrides:
            # Treat the override lemma as a single bare-lemma stem only if
            # it would otherwise pass the alpha+length filter; hyphenated
            # legal terms are atomic by curation, so they DO NOT decompose
            # into bare-lemma siblings. Skip group registration entirely
            # so e.g. 'cross-examination' does not inflate any 'cross' or
            # 'examination' synthetic cluster.
            continue
        doc = nlp(e.canonical)
        # UNION-OF-TOKEN-LEMMAS (RESEARCH §H-5 SPEC AMENDMENT to CONTEXT D-04):
        # collect ALL alpha-token lemmas of length >= 4. The first-token
        # alternative misses 'hearsay' from 'admissible hearsay'.
        lemmas = {
            t.lemma_.lower() for t in doc
            if t.is_alpha and len(t.lemma_) >= 4
        }
        for lem in lemmas:
            groups[lem].add(e.canonical)

    synthetics: list[SyntheticEntry] = []
    for stem, names in groups.items():
        # Rule (a): ≥3 sibling canonicals share the stem.
        if len(names) < 3:
            continue
        # Rule (c): stem length ≥ 4.
        if len(stem) < 4:
            continue
        # Rule (b): bare stem is not already a canonical.
        if stem in canon_set:
            continue
        # Rule (d): stem not in stopwords.
        if stem in stopwords:
            continue

        sibs = [e for e in entries if e.canonical in names]
        synthetics.append(SyntheticEntry(
            stem=stem,
            sibling_canonicals=tuple(sorted(names)),
            locators=_dedupe_locators(sibs),
        ))

    synthetics.sort(key=lambda s: s.stem)
    return synthetics
