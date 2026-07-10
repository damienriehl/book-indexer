"""Lemma+acronym dedup: walks Phase 3a candidates → bucket dict.

Phase 4's intake stage. Pure function over an in-memory list of
``ConceptCandidate``; the file-I/O wrapper that walks
``artifacts/concepts/*.json`` lives in ``tree.py`` (Wave 3).

Algorithm: RESEARCH §H-2 verbatim.

  1. Filter tokenizer artifacts (P-8): standalone digits, punctuation-
     spaced strings, length<3.
  2. Apply B-01 prose-form normalization at intake (so
     "Federal Rule of Evidence 706" joins the "FRE 706" bucket).
  3. Apply B-02 whitespace collapse defensively.
  4. Compute ``lemma_bucket_key`` per surviving canonical_form.
  5. Force-merge acronym buckets via ``fixtures/acronym_overrides.yaml``
     (acronym → spelled-out lemma key); the acronym surfaces become
     variants of the spelled-out canonical (D-01).
  6. D-04 deconfliction: drop buckets where EVERY surface matches a
     table pattern (FRE/FRCP/FRAP/Fed.R./MRPC/Amendment/U.S.Const/USC/
     case-name); KEEP and FLAG buckets where any surface is a derived
     concept (e.g. "Strickland prejudice").

The output ``BucketCandidate`` is a dataclass — NOT a Pydantic model —
because canonical.py inspects (does not mutate) ``surfaces`` for D-01
tiebreakers and a frozen Pydantic model would force tuple casts at every
intermediate step. The IR (``IndexEntry`` etc.) is the public contract;
``BucketCandidate`` is an in-memory intermediate.

D-04 single source of truth (RESEARCH §H-5): the 8 patterns from
``book_indexer.tables.regex_fallback`` are IMPORTED, never redefined.
Two Phase-4-owned patterns (``CASE_NAME_RE``, ``USC_RE``) are defined
here because they are *drop* patterns (subject-index exclusion), not
*extract* patterns (which is the contract of regex_fallback).

requirements_addressed: ASM-01 (lemma+acronym dedup), ASM-09 (table-
citation deconfliction at intake).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import inflect
import yaml

# IMPORTED (NOT redefined) — D-04 single source of truth per RESEARCH §H-5.
from book_indexer.concepts.schema import ConceptCandidate
from book_indexer.tables.regex_fallback import (
    AMENDMENT_PATTERN,
    FEDR_PATTERN,
    FRAP_PATTERN,
    FRCP_PATTERN,
    FRE_PATTERN,
    MRPC_PATTERN,
    PROSE_RULE_PATTERN,
    US_CONST_ART_PATTERN,
)

from .errors import EmptyConceptsError
from .prose_normalize import collapse_whitespace, prose_to_canonical

# Phase 8 / COV-03: shared inflect engine for variant-loss enrichment.
# The engine is stateless w.r.t. callers; lazy module-level instantiation
# is fine. Used by ``_enrich_bucket_with_inflections`` below.
_INFLECT = inflect.engine()

# ---------------------------------------------------------------------------
# Phase-4-owned drop patterns. NOT in regex_fallback — those are extract
# patterns. Per RESEARCH §H-5 these catch surfaces that should be excluded
# from the subject index entirely (their rightful home is a sidecar table).
# ---------------------------------------------------------------------------

CASE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z\.\-']+\s+v\.\s+[A-Za-z]")
USC_RE = re.compile(r"\b\d+\s+U\.?\s*S\.?\s*C\.?\s")


# Order matters only insofar as the first match wins per surface. All match
# attempts are independent; categories are constants.
_TABLE_PATTERNS: list[tuple[re.Pattern[str], Literal["rules", "statutes", "cases"]]] = [
    (FRE_PATTERN, "rules"),
    (FRCP_PATTERN, "rules"),
    (FRAP_PATTERN, "rules"),
    (FEDR_PATTERN, "rules"),
    (PROSE_RULE_PATTERN, "rules"),
    (MRPC_PATTERN, "rules"),
    (AMENDMENT_PATTERN, "statutes"),
    (US_CONST_ART_PATTERN, "statutes"),
    (USC_RE, "statutes"),
    (CASE_NAME_RE, "cases"),
]


# ---------------------------------------------------------------------------
# normalize_for_lemma — D-01 leading-article strip + intra-word hyphen
# collapse (P-7 fix).
# ---------------------------------------------------------------------------

_LEADING_ARTICLES = ("the ", "a ", "an ")
_INTRA_WORD_HYPHEN = re.compile(r"(?<=[a-z])-(?=[a-z])")


def normalize_for_lemma(s: str, phrase_overrides: dict[str, str] | None = None) -> str:
    """D-01 leading-article strip + intra-word hyphen collapse.

    Steps:
        1. ``collapse_whitespace`` then lowercase.
        2. Strip ONE leading article ("the ", "a ", "an ") — only if a
           trailing space follows (so "an" alone returns "an").
        3. Replace intra-word lowercase-letter hyphens with a space (P-7 fix
           for "voir-dire" tokenization). Hyphens between letter-and-digit
           or digit-and-anything are preserved.

    B-10 cross-callsite patch (CONTEXT 06 D-02; Plan 06-02):
    when ``phrase_overrides`` is supplied:
      (a) if the WHOLE string matches a curated key, return its lemma
          directly (covers single-phrase inputs like ``cross-examination``).
      (b) otherwise, before applying ``_INTRA_WORD_HYPHEN.sub``, replace
          each phrase-override SUBSTRING with its lemma after substituting
          any internal hyphens with a sentinel ``\\x00`` so the regex
          leaves them intact; then restore the sentinel back to ``-``
          (covers compound surfaces like ``cross-examination questions``
          where the hyphen is INSIDE a multi-token chunk and the default
          tokenizer/lemmatizer would otherwise re-fragment it).
    """
    s = collapse_whitespace(s).lower()
    for art in _LEADING_ARTICLES:
        if s.startswith(art):
            s = s[len(art):]
            break
    # B-10 (a): whole-string short-circuit.
    if phrase_overrides and s in phrase_overrides:
        return phrase_overrides[s].lower()
    # B-10 (b): substring-protected hyphen preservation. Match longer keys
    # first (greedy) so ``cross-examination`` wins over ``cross``.
    if phrase_overrides:
        for key in sorted(phrase_overrides.keys(), key=len, reverse=True):
            if "-" in key and key in s:
                lemma = phrase_overrides[key].lower()
                # Sentinel-protect lemma hyphens from _INTRA_WORD_HYPHEN.
                protected = lemma.replace("-", "\x00")
                s = s.replace(key, protected)
        return _INTRA_WORD_HYPHEN.sub(" ", s).replace("\x00", "-")
    return _INTRA_WORD_HYPHEN.sub(" ", s)


# ---------------------------------------------------------------------------
# lemma_bucket_key — spaCy lemmatization, joined by space.
# ---------------------------------------------------------------------------


def lemma_bucket_key(s: str, nlp) -> str:
    """Token-by-token lemmatization; join with single space.

    ``nlp`` is a spaCy Language with a tagger+lemmatizer (typically
    ``en_core_web_lg``). We do NOT load it here; ``tree.py`` (Wave 3)
    loads once and passes the instance.

    Empty/whitespace-only input returns ``""``.

    B-10 cross-callsite patch (CONTEXT 06 D-02; Plan 06-02):
      (a) pass ``nlp.meta["_legal_phrase_overrides"]`` into
          ``normalize_for_lemma`` so curated phrase-override hyphens are
          protected from the intra-word-hyphen regex.
      (b) short-circuit ``nlp(base)`` when ``base`` matches a curated phrase
          override (single-phrase input case).
      (c) for compound inputs (e.g. ``cross-examination questions``),
          carve the override surface out of ``base`` BEFORE running
          ``nlp``; lemmatize the residual; then concatenate the override
          lemma + residual lemma — preserving the close-hyphen form that
          the default tokenizer would otherwise re-fragment.
    """
    phrase_overrides = nlp.meta.get("_legal_phrase_overrides") or {} if nlp is not None else {}
    base = normalize_for_lemma(s, phrase_overrides=phrase_overrides)
    if not base:
        return ""
    # B-10 (b): whole-string override.
    if phrase_overrides and base in phrase_overrides:
        return phrase_overrides[base].lower()
    # B-10 (c): compound carve-out. For each override key with a hyphen
    # appearing as a substring in `base`, lemmatize the residual segments
    # separately and stitch with the override lemma. Process ONE override
    # match at a time, longest-first, to avoid greedy-overlap surprises.
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
                left_key = lemma_bucket_key(left, nlp) if left else ""
                right_key = lemma_bucket_key(right, nlp) if right else ""
                parts = [p for p in (left_key, lemma_mid, right_key) if p]
                return " ".join(parts)
    # Caller contract (tree.py Wave 3) always passes a loaded Language.
    assert nlp is not None
    doc = nlp(base)
    # Phase 8 / D-04 cascade (a): apply curated token-level lemma overrides
    # so spaCy verb-lemma collapses (e.g., 'dying' → 'die') don't corrupt
    # bucket keys for candidates like 'dying declaration' / 'losing party'.
    # Without this post-pass, the lemma overrides in
    # config/legal_lemma_overrides.yaml::tokens take effect at ingest time
    # (corpus token lemmas) but NOT at bucket-key time (assembly), leaving
    # the IR canonical as 'die declaration' regardless of the override.
    token_overrides = (
        nlp.meta.get("_legal_token_overrides") or {} if nlp is not None else {}
    )
    lemmas: list[str] = []
    for tok in doc:
        lemma = tok.lemma_
        if not lemma.strip():
            continue
        # Override the lemma if the token's surface form is curated.
        text_lower = tok.text.lower()
        if text_lower in token_overrides:
            lemma = token_overrides[text_lower]
        lemmas.append(lemma.lower())
    return " ".join(lemmas) if lemmas else base


# ---------------------------------------------------------------------------
# filter_tokenizer_artifacts — P-8 noise filter.
# ---------------------------------------------------------------------------

_STANDALONE_DIGIT = re.compile(r"\b\d+\b")
_PUNCT_SPACED = re.compile(r"\s[,.;:]\s")


def filter_tokenizer_artifacts(canonical_form: str) -> bool:
    """Return True if this candidate looks like a tokenizer artifact.

    Caller drops candidates returning True; should be logged to
    provenance under ``dropped_artifacts``.
    """
    s = canonical_form.strip()
    if len(s) < 3:
        return True
    if _STANDALONE_DIGIT.search(s):
        return True
    if _PUNCT_SPACED.search(s):
        return True
    return False


# ---------------------------------------------------------------------------
# D-04 deconfliction classifier.
# ---------------------------------------------------------------------------


def _classify_table_citation(
    surface: str,
) -> Literal["rules", "statutes", "cases"] | None:
    """Return the table category if surface matches a table pattern; else None.

    Used per-surface; a bucket is dropped only if EVERY surface returns
    non-None.
    """
    for pat, category in _TABLE_PATTERNS:
        if pat.search(surface):
            return category
    return None


def is_derived_concept(
    surfaces: Iterable[str],
) -> Literal["cases", "statutes", "rules"] | None:
    """Detect 'Strickland prejudice'-style derived concepts (D-04 KEEP).

    Returns the category if any surface is a known case-first-party token +
    another word; else None. Per RESEARCH §H-5 the source book empirically has
    zero such hits (only 1 case, no derived concepts), but companion
    volumes will exercise this path.

    MVP heuristic: extract the first-party token (left-of-" v.") from any
    surface that matches CASE_NAME_RE; if another surface contains that
    token plus another word AND is not itself a full case-name, classify
    as ``"cases"``.
    """
    surfaces_list = list(surfaces)
    first_parties: set[str] = set()
    for s in surfaces_list:
        m = CASE_NAME_RE.search(s)
        if m:
            head = s.split(" v.")[0].strip()
            if head:
                first_parties.add(head.split()[-1].lower())
    if not first_parties:
        return None
    for s in surfaces_list:
        if CASE_NAME_RE.search(s):
            continue  # full case-name peer; not the derived form
        tokens = s.lower().split()
        if len(tokens) < 2:
            continue
        if any(fp in tokens for fp in first_parties):
            return "cases"
    return None


# ---------------------------------------------------------------------------
# BucketCandidate / SurfaceProvenance dataclasses.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceProvenance:
    """First-appearance positional metadata for D-01 tiebreakers 2-4.

    Frozen by design — Phase 4 expects positional metadata to be immutable
    from intake through canonical election (per Plan 04-01 deviations_allowed).
    """

    section_ref: str
    pdf_page: int
    token_index: int


@dataclass
class BucketCandidate:
    """Pre-canonical bucket. Mutable in-memory; ``canonical.elect_canonical``
    READS but does not modify."""

    lemma_key: str
    surfaces: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)
    suggested_subentries: list[str] = field(default_factory=list)
    pass_types: set[str] = field(default_factory=set)
    derived_from_table: Literal["cases", "statutes", "rules"] | None = None
    surface_provenance: dict[str, SurfaceProvenance] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Acronym fixture loader.
# ---------------------------------------------------------------------------


def load_acronym_overrides(
    path: Path = Path("fixtures/acronym_overrides.yaml"),
) -> dict[str, str]:
    """Read ``fixtures/acronym_overrides.yaml`` → ``{acronym_lower: spelled_out_lower}``.

    Used by ``build_buckets`` to force-merge acronym buckets into spelled-
    out buckets. The YAML's ``metadata.curated_by`` MUST be != PENDING_AUTHOR
    (Wave 0 author sign-off gate) — that invariant is enforced by the Wave 0
    test ``test_author_signed_off``, NOT by this loader.
    """
    data = yaml.safe_load(path.read_text())
    return {row["acronym"].lower(): row["spelled_out"].lower() for row in data["acronyms"]}


# ---------------------------------------------------------------------------
# Phase 8 / COV-03: variant-loss enrichment helper (RESEARCH §Pattern 1).
# ---------------------------------------------------------------------------


def _enrich_bucket_with_inflections(
    bucket: BucketCandidate,
    cand: ConceptCandidate,
    normalized_surface: str,
) -> None:
    """Append every inflected variant of every surface form to bucket.variants.

    Phase 8 / COV-03 — the dominant fix for the variant-loss bug.

    Phase 3a's ``canonical_form`` is often a singularized head form (e.g.
    ``special interrogatory``) while body text uses the plural
    (``Special Interrogatories``). The verifier sweep only sees
    ``bucket.surfaces ∪ bucket.variants ∪ {canonical}``; if neither set
    contains the body-text form, ``verify()`` returns 0 evidence and the
    candidate is dropped to ``provenance.zero_evidence_drops``.

    This helper appends, additively to ``bucket.variants`` ONLY:

      1. ``cand.term`` — the LLM-emitted verbatim corpus surface (the
         field that was previously dropped on the floor; RESEARCH §Pitfall 3).
      2. plural form of every surface's head noun.
      3. singular form of every surface's head noun
         (``inflect.engine.singular_noun`` returns ``False`` if already
         singular; we fall back to the head word in that case so a literal
         ``False`` never reaches ``bucket.variants``).

    Implementation notes:

    - ``bucket.surfaces`` is NOT mutated. Surfaces drives canonical
      election in ``canonical.elect_canonical``; mutating it here could
      shift downstream canonical choices. Variants is the verifier-sweep-
      only path (RESEARCH §Pitfall 3 + Pitfall 1).
    - ``inflect.engine().plural`` is **case-sensitive** in its flip
      behavior. Empirically: ``plural("interrogatories") ==
      "interrogatory"`` (lowercase plural → singular flip works), BUT
      ``plural("Advocates") == "Advocateses"`` and
      ``plural("Pictures") == "Pictureses"`` (Title-case plurals are NOT
      recognized as plural and get re-pluralized into ``*eses`` artifacts).
      Earlier docstring claimed ``inflect.plural`` is a flip-function for
      ALL inputs — that claim was empirically wrong (UAT 08-1b: ~50+
      ``*eses`` artifacts contaminated v1.2 output). The correct guard is
      to ALWAYS classify the head with ``singular_noun(head)`` first:
      if it returns a non-False value, the head is already plural and we
      derive both forms from the singular instead of re-pluralizing the
      already-plural surface.
    - ``singular_noun`` IS case-tolerant — ``singular_noun("Advocates")
      == "Advocate"`` works correctly — so it is a reliable plural
      classifier across surface casings.
    - Lock #1 preserved: this helper does NOT construct Evidence.
      It only enriches the variants list that flows into the existing
      ``verifier_sweep.run_sweep`` mechanism.

    requirements_addressed: COV-03.
    """
    # The LLM-emitted ``term`` field is the verbatim corpus surface — never
    # a normalized canonical. Always include it as a variant if distinct
    # from any surface (RESEARCH §Pitfall 3).
    if (
        cand.term
        and cand.term not in bucket.variants
        and cand.term not in bucket.surfaces
    ):
        bucket.variants.append(cand.term)

    # Pluralize/singularize the head noun of every surface form. Guard
    # against the Title-case-plural pitfall: ``inflect.plural`` is
    # case-sensitive in its flip detection and over-pluralizes inputs like
    # "Advocates" → "Advocateses". Use ``singular_noun`` as the
    # plurality classifier (case-tolerant; returns False iff already
    # singular) to pick the correct (plural, singular) pair.
    seeds = (normalized_surface, cand.term, cand.canonical_form)
    for surface in seeds:
        if not surface:
            continue
        words = surface.split()
        if not words:
            continue
        head = words[-1]
        sing_test = _INFLECT.singular_noun(head)  # pyright: ignore[reportArgumentType]
        if sing_test is False:
            # Head is singular — pluralize directly; sing_head == head.
            plural_head = _INFLECT.plural(head)  # pyright: ignore[reportArgumentType]
            sing_head = head
        else:
            # Head is already plural — use as-is for the plural form;
            # use ``sing_test`` as the singular form. NEVER call
            # ``_INFLECT.plural(head)`` here — it would produce a
            # ``*eses`` over-pluralization artifact (UAT 08-1b).
            plural_head = head
            sing_head = sing_test
        plural_form = " ".join(words[:-1] + [plural_head])
        sing_form = " ".join(words[:-1] + [sing_head])
        for v in (plural_form, sing_form):
            if (
                v
                and v != surface
                and v not in bucket.variants
                and v not in bucket.surfaces
            ):
                bucket.variants.append(v)


# ---------------------------------------------------------------------------
# Top-level bucket builder.
# ---------------------------------------------------------------------------


def build_buckets(
    candidates: list[ConceptCandidate],
    nlp,
    acronym_map: dict[str, str] | None = None,
    candidate_provenance: dict[str, SurfaceProvenance] | None = None,
    section_title_keys: set[str] | None = None,
) -> tuple[dict[str, BucketCandidate], list[dict]]:
    """Produce ``(buckets, dropped_log)``.

    Args:
        candidates: list of ``ConceptCandidate`` (union from all 30 Phase 3a
            artifacts when called from production).
        nlp: spaCy Language (en_core_web_lg).
        acronym_map: optional pre-loaded acronym→spelled-out mapping.
            If None, loads ``fixtures/acronym_overrides.yaml``.
        candidate_provenance: optional ``{canonical_form: SurfaceProvenance}``.
            If absent (typical in unit tests), ``surface_provenance`` is
            empty — D-01 tiebreakers 2-4 fall through to alphabetical, which
            is acceptable for unit tests (deterministic by-construction).
        section_title_keys: optional set of normalized section-title strings
            (lowercased, leading-article-stripped — same normalization as
            ``normalize_for_lemma``). Any candidate whose normalized
            canonical_form is in this set is dropped at intake (ASM-09:
            section titles belong to the structure, not the subject index).
            Plan 04-04-fix BUG 2.

    Returns:
        ``(buckets, dropped_log)``. ``buckets`` is the surviving dict;
        ``dropped_log`` contains entries either with shape
        ``{"surface": str, "reason": "tokenizer_artifact"}`` or
        ``{"lemma_key": str, "surfaces": list[str], "matched_category": str}``
        (D-04 drop) ready for serialization into IndexTreeProvenance.

    Raises:
        EmptyConceptsError: if candidates is empty (RESEARCH §H-13).
    """
    if not candidates:
        raise EmptyConceptsError("artifacts/concepts/ has no candidates")

    if acronym_map is None:
        acronym_map = load_acronym_overrides()

    candidate_provenance = candidate_provenance or {}
    section_title_keys = section_title_keys or set()

    buckets: dict[str, BucketCandidate] = {}
    dropped_artifacts: list[dict] = []
    dropped_section_titles: list[dict] = []

    # ---- Pass 1: filter tokenizer artifacts; normalize; compute lemma_key; group ----
    for cand in candidates:
        cf = cand.canonical_form.strip()

        # B-01 normalization at intake: prose-form → canonical citation form,
        # so "Federal Rule of Evidence 706" joins the "FRE 706" bucket.
        normalized_surface = prose_to_canonical(cf) or cf
        # B-02: collapse internal whitespace defensively.
        normalized_surface = collapse_whitespace(normalized_surface)

        # Tokenizer-artifact filter (P-8). Skip the filter when the surface
        # matches a table pattern (e.g. "FRE 401" trivially trips standalone-
        # digit detection but is a legitimate citation that D-04 will handle
        # downstream). Run the filter against BOTH the original and
        # normalized form so prose-form normalization doesn't smuggle junk
        # into a bucket.
        if _classify_table_citation(normalized_surface) is None:
            if filter_tokenizer_artifacts(cf) or filter_tokenizer_artifacts(
                normalized_surface
            ):
                dropped_artifacts.append({"surface": cf, "reason": "tokenizer_artifact"})
                continue

        # ASM-09 (Plan 04-04-fix BUG 2): drop candidates whose normalized
        # surface equals a section title from the corpus. Section titles
        # belong to the document's structural skeleton — they must not
        # appear as canonical entries in the subject index. We compare on
        # the same normalization used for lemma_bucket_key (lowercase,
        # leading article stripped, intra-word hyphen → space).
        #
        # B-10 carve-out (CONTEXT 06 D-02; Plan 06-02 cross-callsite patch):
        # candidates whose lowercased+stripped form (or normalized surface)
        # matches a curated phrase override in nlp.meta are CURATOR-
        # APPROVED subject-index content and BYPASS section-title
        # filtering. Without this carve-out, terms like ``cross-examination``
        # — which appear as BOTH a section title AND a 100-occurrence body
        # subject — get dropped at intake despite explicit YAML curation.
        phrase_overrides = (
            nlp.meta.get("_legal_phrase_overrides") or {} if nlp is not None else {}
        )
        if section_title_keys:
            cf_lower = cf.lower().strip()
            norm_surface_lower = normalized_surface.lower().strip()
            curator_approved = bool(phrase_overrides) and (
                cf_lower in phrase_overrides
                or norm_surface_lower in phrase_overrides
            )
            if not curator_approved:
                norm_cf = normalize_for_lemma(cf, phrase_overrides=phrase_overrides)
                norm_surface = normalize_for_lemma(
                    normalized_surface, phrase_overrides=phrase_overrides
                )
                if (
                    norm_cf in section_title_keys
                    or norm_surface in section_title_keys
                ):
                    dropped_section_titles.append(
                        {"surface": cf, "reason": "section_title"}
                    )
                    continue

        key = lemma_bucket_key(normalized_surface, nlp)
        if not key:
            continue
        bucket = buckets.setdefault(key, BucketCandidate(lemma_key=key))
        if normalized_surface not in bucket.surfaces:
            bucket.surfaces.append(normalized_surface)
        # Phase 8 / COV-03: enrich variants with cand.term + inflections so
        # singular ↔ plural body-text forms verify against the canonical
        # bucket. See ``_enrich_bucket_with_inflections`` for full rationale.
        _enrich_bucket_with_inflections(bucket, cand, normalized_surface)
        for v in cand.variants:
            if v not in bucket.variants:
                bucket.variants.append(v)
        for sub in (cand.suggested_subentries or []):
            if sub not in bucket.suggested_subentries:
                bucket.suggested_subentries.append(sub)
        bucket.pass_types.add(getattr(cand, "pass_type", "noun_phrase"))
        if cf in candidate_provenance:
            bucket.surface_provenance[normalized_surface] = candidate_provenance[cf]

    # ---- Pass 2: acronym force-merge ----
    # For every entry in the acronym map: if both the acronym lemma_key and
    # the spelled-out lemma_key exist as buckets, merge the acronym bucket
    # INTO the spelled-out bucket. The acronym surfaces become variants of
    # the spelled-out canonical (D-01: acronyms-as-variants).
    for acronym_l, spelled_l in acronym_map.items():
        acronym_key = lemma_bucket_key(acronym_l, nlp)
        spelled_key = lemma_bucket_key(spelled_l, nlp)
        if (
            acronym_key
            and spelled_key
            and acronym_key in buckets
            and spelled_key in buckets
            and acronym_key != spelled_key
        ):
            src = buckets.pop(acronym_key)
            tgt = buckets[spelled_key]
            for s in src.surfaces:
                if s not in tgt.variants and s not in tgt.surfaces:
                    tgt.variants.append(s)
            for v in src.variants:
                if v not in tgt.variants and v not in tgt.surfaces:
                    tgt.variants.append(v)
            for sub in src.suggested_subentries:
                if sub not in tgt.suggested_subentries:
                    tgt.suggested_subentries.append(sub)
            tgt.pass_types.update(src.pass_types)
            for k, v in src.surface_provenance.items():
                tgt.surface_provenance.setdefault(k, v)

    # ---- Pass 3: D-04 deconfliction (drop or flag) ----
    dropped_table_citations: list[dict] = []
    for key in list(buckets.keys()):
        bucket = buckets[key]
        classes: list[Literal["rules", "statutes", "cases"] | None] = [
            _classify_table_citation(s) for s in bucket.surfaces
        ]
        classes_v: list[Literal["rules", "statutes", "cases"] | None] = [
            _classify_table_citation(v) for v in bucket.variants
        ]
        all_match = bool(classes) and all(c is not None for c in classes)

        if all_match:
            first_cat = next((c for c in classes if c is not None), None)
            dropped_table_citations.append(
                {
                    "lemma_key": key,
                    "surfaces": list(bucket.surfaces),
                    "matched_category": first_cat,
                }
            )
            buckets.pop(key)
            continue

        non_none: list[Literal["rules", "statutes", "cases"]] = [
            c for c in (classes + classes_v) if c is not None
        ]
        if non_none:
            bucket.derived_from_table = non_none[0]
        else:
            derived = is_derived_concept(bucket.surfaces + bucket.variants)
            if derived:
                bucket.derived_from_table = derived

    return (
        buckets,
        dropped_artifacts + dropped_section_titles + dropped_table_citations,
    )
