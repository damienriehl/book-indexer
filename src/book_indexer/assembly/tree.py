"""IndexTree assembler — composes Wave 1-2 modules per RESEARCH §P-5.

This module is the SOLE Phase 4 orchestrator: it walks
``artifacts/concepts/*.json``, dedupes via ``dedup.build_buckets``, elects
canonicals via ``canonical.elect_canonical``, runs the verifier sweep via
``verifier_sweep.run_sweep`` (Lock #1: tree.py never calls verify()
directly — only through this transitive import), drops zero-evidence
buckets, builds locators via ``cite_rule.cite_for_canonical``, subdivides
oversize parents via ``subdivide.subdivide_oversize``, builds cross-refs
via ``cross_refs.build_*``, validates the graph, and emits a frozen
``IndexTree`` plus an evidence ledger keyed by 1-based ``evidence_id``.

Assembly order (RESEARCH §P-5; do NOT reorder — zero-evidence drops MUST
precede cross-ref construction or see/see_also targets dangle):

  1. Walk concepts artifacts → ConceptCandidate union.
  2. Build surface_provenance map from corpus.tokens (D-01 tiebreakers).
  3. dedup.build_buckets → buckets + dropped_log.
  4. canonical.elect_canonical per bucket → canonical surface.
  5. verifier_sweep.run_sweep → Evidence per bucket.
  6. Drop zero-evidence buckets (record lemma_keys in provenance).
  7. cite_rule.cite_for_canonical → Locators (placeholder evidence_id).
  8. subdivide.subdivide_oversize for any with >7 locators.
  9. Build evidence ledger (1-based ids); replace placeholder ids.
 10. Construct preliminary IndexEntry objects.
 11. cross_refs.build_see_edges + build_see_also_edges.
 12. Re-construct IndexEntry with see/see_also filled.
 13. cross_refs.validate_graph (cycles, dangling, out-degree).
 14. Compute IndexTreeProvenance + sort entries by sort_key.
 15. Construct frozen IndexTree.

Architecture Lock #1: this module imports ``run_sweep`` from
``verifier_sweep`` (the SOLE verify()-caller) but does NOT import or call
``book_indexer.verify.verify`` directly. It also never CONSTRUCTS
``Evidence`` — only reads attributes off Evidence rows returned by
``run_sweep`` (.section_ref, .folio, .canonical_term, etc.) when building
evidence-ledger payloads. Verified by ship-blocker grep tests.

requirements_addressed: ASM-01..ASM-09 (full composition); orchestration
of all Wave 1-2 modules.
"""
from __future__ import annotations

import importlib.metadata as md
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from book_indexer.concepts.schema import (
    ConceptCandidate,
    ConceptDiscoveryResponse,
)
from book_indexer.tables.ir import Locator
from book_indexer.verify.evidence import Evidence

from . import coverage as _coverage
from .canonical import elect_canonical, strip_leading_article
from .cite_rule import cite_for_canonical
from .cross_refs import (
    build_see_also_edges,
    build_see_edges,
    validate_graph,
)
from .dedup import (
    BucketCandidate,
    SurfaceProvenance,
    build_buckets,
    load_acronym_overrides,
    normalize_for_lemma,
)
from .ir import IndexEntry, IndexTree, IndexTreeProvenance, SubEntry
from .subdivide import subdivide_oversize
from .verifier_sweep import EvidenceByCanonical, run_sweep

# ---------------------------------------------------------------------------
# Slug + sort_key helpers (D-07 + RESEARCH §H-10).
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_OVERSIZE_THRESHOLD = 7


def slugify(s: str) -> str:
    """D-07 slug: lowercase, collapse non-alphanumeric → hyphen, strip ends.

    Empty input returns ``"x"`` (defensive — production callers should
    never pass empty, but the IndexEntry.id pattern requires non-empty).
    Mirrors ``cross_refs._slugify`` byte-for-byte (no shared import to avoid
    circular dependency with cross_refs at module load).
    """
    s = s.lower().strip()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s or "x"


def compute_id(canonical: str, taken: set[str]) -> str:
    """Compute the IndexEntry.id for ``canonical`` with collision suffix.

    First call slugifies the canonical. If the slug is already in
    ``taken``, append ``-2`` then ``-3`` and so on until unique. Mutates
    ``taken`` to record the chosen id.

    D-07: collision suffixes are assigned in caller-controlled order;
    ``tree.build_index_tree`` calls ``compute_id`` while iterating
    canonicals in alphabetical order so the second alphabetical canonical
    receives ``-2``, third receives ``-3``, etc.
    """
    base = slugify(canonical)
    if base not in taken:
        taken.add(base)
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    candidate = f"{base}-{i}"
    taken.add(candidate)
    return candidate


def compute_sort_key(canonical: str) -> str:
    """D-07 letter-by-letter alphabetical sort key.

    Strip ONE leading article ("the ", "a ", "an ") via
    ``canonical.strip_leading_article``; lowercase. The IR's ``sort_key``
    field is exactly this string — entries are sorted by it at IndexTree
    emit time.
    """
    return strip_leading_article(canonical).lower()


# ---------------------------------------------------------------------------
# Concepts artifact walker.
# ---------------------------------------------------------------------------


def _walk_concept_artifacts(
    concepts_dir: Path,
) -> tuple[list[ConceptCandidate], dict[str, str]]:
    """Walk ``concepts_dir/*.json``; return (candidates, file_sha_map).

    ``file_sha_map`` maps ``filename → sha256`` for IndexTreeProvenance.
    Skips ``*.provenance.json`` sidecars (Phase 3a v2 emits 30 files: 15
    artifacts + 15 sidecars; we want only the artifacts).

    Each artifact validates through ``ConceptDiscoveryResponse``; we
    flatten ``response.candidates`` into a single list and tag each
    candidate with its ``pass_type`` (via dynamic attribute) so dedup can
    track which buckets carry "noun_phrase" provenance for the subdivide
    pool.
    """
    import hashlib

    candidates: list[ConceptCandidate] = []
    file_sha_map: dict[str, str] = {}

    for path in sorted(concepts_dir.glob("*.json")):
        if path.name.endswith(".provenance.json"):
            continue
        raw = path.read_bytes()
        file_sha_map[path.name] = hashlib.sha256(raw).hexdigest()
        envelope = ConceptDiscoveryResponse.model_validate_json(raw)
        # Tag each candidate with its pass_type via attribute (frozen
        # Pydantic forbids assignment, so we re-validate with extra fields
        # — instead, we attach pass_type to a parallel dict keyed by
        # canonical_form. dedup.build_buckets already detects pass_type
        # via getattr fallback to "noun_phrase").
        # Approach: rebuild a dict with pass_type on each candidate using
        # the underlying schema's frozen model — easier: keep the model
        # frozen and pass pass_type via a sidecar parallel list, but
        # dedup.build_buckets reads cand.pass_type if present. We use
        # object.__setattr__ to bypass Pydantic's frozen check (defensive
        # — the alternative would be a wrapper dataclass; this preserves
        # the validation we already paid for).
        for cand in envelope.candidates:
            object.__setattr__(cand, "pass_type", envelope.pass_type)
            candidates.append(cand)

    return candidates, file_sha_map


# ---------------------------------------------------------------------------
# Surface-provenance builder (corpus.tokens lookup for D-01 tiebreakers).
# ---------------------------------------------------------------------------


def _build_surface_provenance(
    candidates: list[ConceptCandidate],
    corpus_path: Path,
) -> dict[str, SurfaceProvenance]:
    """Look up first-occurrence (section_ref, pdf_page, token_offset) per
    canonical_form by querying ``corpus.tokens.norm`` against
    ``normalize_for_lemma(canonical_form)``.

    Returns ``{canonical_form: SurfaceProvenance}``. Surfaces with no hit
    are absent from the map (D-01 tiebreakers 2-4 fall through to
    alphabetical, per ``canonical._provenance_key``).

    Read-only sqlite open; closes the connection at exit.
    """
    if not corpus_path.exists():
        return {}
    conn = sqlite3.connect(f"file:{corpus_path}?mode=ro", uri=True)
    out: dict[str, SurfaceProvenance] = {}
    try:
        # Build the set of normalized forms once; dedupe to avoid redundant
        # queries.
        seen_norms: dict[str, str] = {}  # norm → original canonical_form
        for cand in candidates:
            cf = cand.canonical_form.strip()
            norm = normalize_for_lemma(cf)
            if norm and norm not in seen_norms:
                seen_norms[norm] = cf

        # Phase 1's tokens table schema uses 'norm' for the normalized
        # form; we look up the lowest (pdf_page, token_offset) and join
        # to sections for the section_ref. If the schema differs slightly,
        # we degrade gracefully (return empty provenance).
        try:
            for norm, cf in seen_norms.items():
                row = conn.execute(
                    "SELECT t.pdf_page, t.token_offset, t.section_ref "
                    "FROM tokens t "
                    "WHERE t.norm = ? "
                    "ORDER BY t.pdf_page ASC, t.token_offset ASC LIMIT 1",
                    (norm,),
                ).fetchone()
                if row is None:
                    continue
                pdf_page, token_offset, section_ref = row
                if not section_ref:
                    continue
                out[cf] = SurfaceProvenance(
                    section_ref=section_ref,
                    pdf_page=int(pdf_page),
                    token_index=int(token_offset),
                )
        except sqlite3.OperationalError:
            # Schema drift — degrade to empty provenance (canonical falls
            # through to alphabetical tiebreaker, still deterministic).
            return {}
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
# Section-title loader (ASM-09: drop section-title canonicals at intake).
# ---------------------------------------------------------------------------


def _load_section_titles_to_exclude(
    sections_fixture: Path,
    phrase_overrides: dict[str, str] | None = None,
) -> set[str]:
    """Load section titles from the fixture YAML; return normalized set.

    Plan 04-04-fix BUG 2 (ASM-09): the dedup intake must drop any
    candidate whose normalized canonical_form equals a section title
    from any level (chapter, major, sub) in
    ``fixtures/sections.yaml``. Phase 1 captured these section
    titles; their natural home is the document's structural skeleton,
    not the subject index. Returning an empty set is safe (no filtering
    is applied) — used in unit-test contexts that don't need section-
    title filtering.

    Normalization mirrors :func:`dedup.normalize_for_lemma` (lowercase,
    strip leading article, intra-word hyphen → space) so the comparison
    is symmetric with the candidate-side normalization.

    B-10 (CONTEXT 06 D-02; Plan 06-02 cross-callsite patch):
    ``phrase_overrides`` is forwarded to ``normalize_for_lemma`` so
    section titles like ``Cross-Examination`` retain the close-hyphen
    form rather than collapsing to ``cross examination``. The candidate-
    side normalization in ``build_buckets`` does the same thing, so the
    two sides remain symmetric. (The curator-approved bypass in
    ``build_buckets`` makes this symmetry largely cosmetic for the
    overridden terms, but consistency is cheap.)
    """
    if not sections_fixture.exists():
        return set()
    import yaml

    raw = yaml.safe_load(sections_fixture.read_text(encoding="utf-8")) or {}
    titles: set[str] = set()
    for s in raw.get("sections", []):
        title = (s.get("title") or "").strip()
        if not title:
            continue
        titles.add(normalize_for_lemma(title, phrase_overrides=phrase_overrides))
    titles.discard("")
    return titles


# ---------------------------------------------------------------------------
# Canonical chooser closure (returns map BucketCandidate.lemma_key → canonical).
# ---------------------------------------------------------------------------


def _choose_canonicals(
    buckets: dict[str, BucketCandidate],
    nlp,
) -> dict[str, str]:
    """Run ``elect_canonical`` per bucket; return ``{lemma_key: canonical}``."""
    return {key: elect_canonical(bucket, nlp) for key, bucket in buckets.items()}


# ---------------------------------------------------------------------------
# Evidence-ledger emit + Locator placeholder fix-up.
# ---------------------------------------------------------------------------


def _evidence_payload(ev: Evidence, eid: int) -> dict:
    """Serialize an Evidence row to a JSON payload + 1-based id.

    Read-only attribute access; no Evidence construction (Lock #1).
    """
    payload = ev.model_dump(mode="json")
    return {"id": eid, **payload}


def _rewrite_locators_to_rep_evidence(
    locators_per_bucket: dict[str, list[Locator]],
    evidence_per_bucket: dict[str, list[Evidence]],
) -> dict[str, list[Locator]]:
    """BUG 1 fix (ASM-02): rewrite each Locator.(section_ref, folio) to the
    per-chapter representative Evidence row's actual surface data.

    ``cite_for_canonical`` emits Locators carrying the cluster's LCA
    section_ref (display intent); ASM-02 requires the Locator's
    ``(section_ref, folio)`` to equal the joined Evidence row's exact
    ``(section_ref, folio)``. We compute the same per-chapter rep that
    will later be written into the evidence ledger and rewrite the
    Locator's surface data accordingly. The ``evidence_id`` placeholder
    is preserved — :func:`_attach_evidence_ids` replaces it later with
    the actual ledger row id.

    Lock #1 boundary: this function reads attributes off Evidence rows
    (.section_ref, .folio, .pdf_page, .token_offset, .section_path) but
    constructs only Locators (Locator IS NOT Evidence — Locator is the
    surface IR row that joins TO the evidence ledger via evidence_id).
    """
    out: dict[str, list[Locator]] = {}
    for key, locs in locators_per_bucket.items():
        evidence_list = evidence_per_bucket.get(key, [])
        if not locs or not evidence_list:
            out[key] = list(locs)
            continue
        # Per-chapter representative: min(pdf_page, token_offset) — must
        # mirror :func:`_attach_evidence_ids`'s rep selection exactly.
        by_chapter: dict[int, Evidence] = {}
        for ev in evidence_list:
            try:
                chap = int(ev.section_path[0].lstrip("§"))
            except (IndexError, ValueError):
                chap = 0
            cur = by_chapter.get(chap)
            if cur is None or (ev.pdf_page, ev.token_offset) < (
                cur.pdf_page,
                cur.token_offset,
            ):
                by_chapter[chap] = ev
        # locators from cite_rule are already chapter-sorted ascending;
        # iterate parallel.
        chapters_sorted = sorted(by_chapter.keys())
        rewritten: list[Locator] = []
        for loc, chap in zip(locs, chapters_sorted):
            rep = by_chapter[chap]
            rewritten.append(
                Locator(
                    section_ref=rep.section_ref,
                    folio=rep.folio,
                    evidence_id=loc.evidence_id,  # placeholder preserved
                )
            )
        out[key] = rewritten
    return out


def _attach_evidence_ids(
    locators_per_bucket: dict[str, list[Locator]],
    evidence_per_bucket: dict[str, list[Evidence]],
) -> tuple[dict[str, list[Locator]], list[dict]]:
    """Replace the cite_rule placeholder evidence_id with actual ledger ids.

    Locators must already have been rewritten through
    :func:`_rewrite_locators_to_rep_evidence` so each Locator.(section_ref,
    folio) equals its representative Evidence row's actual surface data
    (BUG 1 fix / ASM-02). This function then assigns ledger ids via the
    same per-chapter rep selection.

    Returns ``(fixed_locators_by_bucket, ledger_rows_in_emit_order)``.
    Ledger order: bucket lemma_key alpha; within a bucket, by
    (section_ref, folio).
    """
    fixed: dict[str, list[Locator]] = {}
    ledger: list[dict] = []
    eid_counter = 0

    for lemma_key in sorted(locators_per_bucket.keys()):
        locators = locators_per_bucket[lemma_key]
        evidence_list = evidence_per_bucket.get(lemma_key, [])
        if not locators or not evidence_list:
            fixed[lemma_key] = []
            continue

        # Group Evidence by chapter; pick min(pdf_page, token_offset) per
        # chapter (mirrors cite_rule.cite_for_canonical's "rep" choice).
        by_chapter: dict[int, Evidence] = {}
        for ev in evidence_list:
            try:
                chap = int(ev.section_path[0].lstrip("§"))
            except (IndexError, ValueError):
                chap = 0
            cur = by_chapter.get(chap)
            if cur is None or (ev.pdf_page, ev.token_offset) < (
                cur.pdf_page,
                cur.token_offset,
            ):
                by_chapter[chap] = ev

        # Build per-chapter Evidence row → ledger id; sort by (section_ref,
        # folio) so the ledger is deterministic.
        chap_to_eid: dict[int, int] = {}
        chosen_evs = sorted(
            by_chapter.items(),
            key=lambda kv: (kv[1].section_ref, kv[1].folio),
        )
        for chap, ev in chosen_evs:
            eid_counter += 1
            chap_to_eid[chap] = eid_counter
            ledger.append(_evidence_payload(ev, eid_counter))

        # Re-emit Locators with the actual evidence_id, keyed by chapter.
        # Locators from the rewrite step already carry rep_ev's
        # (section_ref, folio); we preserve them so the Locator-↔-ledger
        # join matches byte-for-byte (ASM-02).
        new_locs: list[Locator] = []
        chapters_sorted = sorted(by_chapter.keys())
        for loc, chap in zip(locators, chapters_sorted):
            eid = chap_to_eid[chap]
            new_locs.append(
                Locator(
                    section_ref=loc.section_ref,
                    folio=loc.folio,
                    evidence_id=eid,
                )
            )
        fixed[lemma_key] = new_locs

    return fixed, ledger


# ---------------------------------------------------------------------------
# Top-level builder.
# ---------------------------------------------------------------------------


def build_index_tree(
    concepts_dir: Path,
    corpus_path: Path,
    nlp,
    pdf_sha256: str,
    tables_sha: dict[str, str] | None = None,
    acronym_map: dict[str, str] | None = None,
    max_workers: int = 8,
    sections_fixture: Path | None = None,
) -> tuple[IndexTree, list[dict]]:
    """Compose Wave 1-2 modules → ``(IndexTree, evidence_ledger_rows)``.

    Args:
        concepts_dir: Directory of ``artifacts/concepts/*.json`` files
            (15 Phase 3a v2 artifacts; provenance sidecars skipped).
        corpus_path: Path to ``artifacts/page_corpus.sqlite`` (read-only).
        nlp: spaCy Language (en_core_web_lg + EntityRuler).
        pdf_sha256: hex digest of the source PDF (for provenance).
        tables_sha: ``{filename: sha256}`` for the Phase 3b table artifacts
            (cases.json, statutes.json, rules.json). Optional; defaults
            to {} when callers don't track tables.
        acronym_map: optional pre-loaded acronym→spelled-out map. If
            None, loads ``fixtures/acronym_overrides.yaml``.
        max_workers: ProcessPoolExecutor worker count for verifier_sweep.
        sections_fixture: optional path to ``fixtures/sections.yaml``;
            when provided, every section title (lowercased + leading-article-
            stripped) is excluded from the subject index at intake (ASM-09;
            Plan 04-04-fix BUG 2). When None, defaults to
            ``fixtures/sections.yaml`` relative to repo root if
            present, else falls through to no filtering (acceptable for
            unit tests).

    Returns:
        ``(IndexTree, evidence_ledger_rows)``. ``evidence_ledger_rows`` is
        a list of dicts ``{"id": 1, **Evidence.model_dump(mode="json")}``
        ready for orjson emit.

    Raises:
        EmptyConceptsError: from ``dedup.build_buckets`` if no candidates.
        CycleDetectedError / DanglingRefError: from ``validate_graph``.
        OversizeAfterIterationError: from ``subdivide_oversize``.
    """
    tables_sha = tables_sha or {}

    # 1. Walk concepts/*.json
    candidates, concepts_sha = _walk_concept_artifacts(concepts_dir)
    pre_dedup_count = len(candidates)

    # 2. Surface-provenance for D-01 tiebreakers
    surface_prov = _build_surface_provenance(candidates, corpus_path)

    # 2b. Section-title exclusion set (ASM-09 / Plan 04-04-fix BUG 2).
    # Default lookup: fixtures/sections.yaml relative to repo root
    # (corpus_path is a sibling under artifacts/, so its parent is repo root).
    if sections_fixture is None:
        default_fixture = (
            corpus_path.parent.parent / "fixtures" / "sections.yaml"
        )
        sections_fixture = default_fixture if default_fixture.exists() else None
    # B-10 (CONTEXT 06 D-02; Plan 06-02): forward nlp.meta phrase_overrides
    # so the section-title set retains close-hyphen forms (cross-examination
    # etc.) symmetrically with the candidate-side normalization.
    _phrase_overrides = nlp.meta.get("_legal_phrase_overrides") or {} if nlp is not None else {}
    section_title_keys = (
        _load_section_titles_to_exclude(sections_fixture, phrase_overrides=_phrase_overrides)
        if sections_fixture is not None
        else set()
    )

    # 3. Dedup
    buckets, dropped_log = build_buckets(
        candidates,
        nlp,
        acronym_map=acronym_map,
        candidate_provenance=surface_prov,
        section_title_keys=section_title_keys,
    )
    post_dedup_count = len(buckets)
    dropped_table_citations = [d for d in dropped_log if "lemma_key" in d]
    post_deconflict_count = post_dedup_count

    # 4. Elect canonicals
    canonical_by_key = _choose_canonicals(buckets, nlp)

    # 5. Verifier sweep (Lock #1 boundary — the SOLE verify() caller)
    evidence_by_key = run_sweep(
        buckets,
        canonical_chooser=lambda b: canonical_by_key[b.lemma_key],
        corpus_path=str(corpus_path),
        max_workers=max_workers,
    )

    # 6. Drop zero-evidence buckets (P-5 ordering: BEFORE cross-refs).
    zero_evidence_drops: list[str] = []
    surviving_keys: list[str] = []
    for key in buckets.keys():
        evs = evidence_by_key.get(key, [])
        if not evs:
            zero_evidence_drops.append(key)
        else:
            surviving_keys.append(key)
    post_zero_evidence_count = len(surviving_keys)

    # 7. cite_rule per surviving bucket
    locators_by_key: dict[str, list[Locator]] = {}
    for key in surviving_keys:
        locators_by_key[key] = cite_for_canonical(evidence_by_key[key])

    # 7b. BUG 1 fix (Plan 04-04-fix, ASM-02): rewrite each Locator's
    # section_ref + folio to match the per-chapter representative
    # Evidence row's actual surface data. cite_for_canonical promotes
    # to the LCA section_ref (cluster display intent), but the joined
    # Evidence row sits at a deeper section_ref; ASM-02 ship-blocker
    # requires Locator.(section_ref, folio) ≡ ledger row.(section_ref,
    # folio). We rewrite the locators here — BEFORE subdivide — so the
    # noun_phrase pool and parent locators stay in sync (both use rep_ev
    # surface data, so subdivide's section_ref ∩ filter still works).
    locators_by_key = _rewrite_locators_to_rep_evidence(
        locators_by_key,
        {k: evidence_by_key[k] for k in surviving_keys},
    )

    # 8. Build noun_phrase pool for subdivide. D-05: ONLY noun_phrase
    # candidates are eligible as sub-entries (NER/doctrinal are excluded
    # because they typically already are canonicals themselves).
    noun_phrase_pool: dict[str, list[Locator]] = {}
    for key in surviving_keys:
        bucket = buckets[key]
        if "noun_phrase" in bucket.pass_types and locators_by_key[key]:
            # Pool key is the canonical surface (display form); subdivide
            # uses it verbatim as SubEntry.text.
            canon = canonical_by_key[key]
            noun_phrase_pool[canon] = locators_by_key[key]

    # 9. Subdivide oversize parents
    sub_entries_by_key: dict[str, list[SubEntry]] = {}
    residual_by_key: dict[str, list[Locator]] = {}
    oversize_parent_count = 0
    sub_entry_total_count = 0
    max_sub_per_parent = 0
    iteration_depth = 1

    for key in surviving_keys:
        canon = canonical_by_key[key]
        bucket = buckets[key]
        locs = locators_by_key[key]
        if len(locs) > _OVERSIZE_THRESHOLD:
            sub_entries, residual = subdivide_oversize(
                canonical_id=canon,
                parent_locators=locs,
                suggested_subentries=bucket.suggested_subentries,
                noun_phrase_pool=noun_phrase_pool,
            )
            sub_entries_by_key[key] = sub_entries
            residual_by_key[key] = residual
            if sub_entries:
                oversize_parent_count += 1
                sub_entry_total_count += len(sub_entries)
                max_sub_per_parent = max(max_sub_per_parent, len(sub_entries))
                iteration_depth = max(iteration_depth, 1)  # subdivide caps at 2
        else:
            sub_entries_by_key[key] = []
            residual_by_key[key] = locs

    # 10. Attach evidence ids — replace placeholders with ledger ids.
    fixed_locators, ledger_rows = _attach_evidence_ids(
        residual_by_key, {k: evidence_by_key[k] for k in surviving_keys}
    )
    # Sub-entry locators reference the same evidence rows; rebuild them
    # against the ledger by section_ref+folio match.
    eid_index: dict[tuple[str, str], int] = {
        (row["section_ref"], row["folio"]): row["id"] for row in ledger_rows
    }
    fixed_sub_entries: dict[str, list[SubEntry]] = {}
    for key, subs in sub_entries_by_key.items():
        new_subs: list[SubEntry] = []
        for sub in subs:
            new_locs: list[Locator] = []
            for loc in sub.locators:
                eid = eid_index.get((loc.section_ref, loc.folio))
                if eid is None:
                    # Sub-entry references a section_ref+folio not in the
                    # parent's ledger — this happens when the noun_phrase
                    # pool contains a Locator whose evidence wasn't drawn
                    # from the parent's bucket (the pool is union over all
                    # surviving buckets). Skip such locators (defensive;
                    # sub-entries with no surviving locators are dropped).
                    continue
                new_locs.append(
                    Locator(
                        section_ref=loc.section_ref,
                        folio=loc.folio,
                        evidence_id=eid,
                    )
                )
            if new_locs:
                new_subs.append(
                    SubEntry(
                        text=sub.text,
                        sort_key=sub.sort_key,
                        locators=sorted(
                            new_locs,
                            key=lambda l: (l.section_ref, l.folio),
                        ),
                    )
                )
        fixed_sub_entries[key] = sorted(new_subs, key=lambda s: s.sort_key)

    # 11. Construct preliminary IndexEntry per bucket
    # First pass: assign ids in alphabetical-canonical order so collision
    # suffixes are deterministic (D-07).
    surviving_canonicals: list[tuple[str, str]] = sorted(
        ((canonical_by_key[k], k) for k in surviving_keys),
        key=lambda x: (x[0].lower(), x[0]),
    )
    taken_ids: set[str] = set()
    id_by_key: dict[str, str] = {}
    slug_collision_count = 0
    for canon, key in surviving_canonicals:
        base = slugify(canon)
        eid = compute_id(canon, taken_ids)
        if eid != base:
            slug_collision_count += 1
        id_by_key[key] = eid

    preliminary_entries: list[IndexEntry] = []
    for key in surviving_keys:
        bucket = buckets[key]
        canon = canonical_by_key[key]
        locs = sorted(
            fixed_locators[key], key=lambda l: (l.section_ref, l.folio)
        )
        # variants = surfaces ∪ variants − {canonical}, sorted (-len, alpha)
        variant_set = set(bucket.surfaces) | set(bucket.variants) - {canon}
        variant_set.discard(canon)
        variants = sorted(variant_set, key=lambda v: (-len(v), v.lower()))
        preliminary_entries.append(
            IndexEntry(
                id=id_by_key[key],
                canonical=canon,
                sort_key=compute_sort_key(canon),
                derived_from_table=bucket.derived_from_table,
                locators=locs,
                sub_entries=fixed_sub_entries.get(key, []),
                see=[],
                see_also=[],
                variants=variants,
            )
        )

    # 12. Build cross-refs
    variants_by_id: dict[str, list[str]] = {
        e.id: list(e.variants) for e in preliminary_entries
    }
    see_inverted = build_see_edges(variants_by_id)
    # build_see_edges returns {variant_slug: [canonical_id, ...]}; we want
    # per-canonical see lists (currently empty: see edges are FROM variant
    # slugs TO canonicals, used by Phase 5 rendering for See cross-refs).
    # The IR's IndexEntry.see field stores forward See pointers — left
    # empty here per D-03 (See targets are rendered from variants, not
    # listed on the canonical entry).
    entry_by_id = {e.id: e for e in preliminary_entries}
    see_also_dict = build_see_also_edges(entry_by_id)

    # 13. Reconstruct entries with see_also filled.
    final_entries: list[IndexEntry] = []
    for entry in preliminary_entries:
        see_also = see_also_dict.get(entry.id, [])
        final_entries.append(
            IndexEntry(
                id=entry.id,
                canonical=entry.canonical,
                sort_key=entry.sort_key,
                derived_from_table=entry.derived_from_table,
                locators=entry.locators,
                sub_entries=entry.sub_entries,
                see=[],
                see_also=see_also,
                variants=entry.variants,
            )
        )

    # 14. Validate cross-ref graph (cycles / dangling / out-degree).
    validate_graph(final_entries)

    # 15. Sort entries by sort_key + final IndexTree assembly.
    final_entries.sort(key=lambda e: (e.sort_key, e.id))

    parents_with_no_locators = sum(1 for e in final_entries if not e.locators)
    oob_status = _coverage.compute_oob_status(len(final_entries))

    try:
        spacy_version = md.version("spacy")
    except md.PackageNotFoundError:  # pragma: no cover
        spacy_version = "unknown"
    try:
        eyecite_version = md.version("eyecite")
    except md.PackageNotFoundError:  # pragma: no cover
        eyecite_version = "unknown"
    try:
        reporters_db_version = md.version("reporters-db")
    except md.PackageNotFoundError:  # pragma: no cover
        reporters_db_version = "unknown"
    try:
        courts_db_version = md.version("courts-db")
    except md.PackageNotFoundError:  # pragma: no cover
        courts_db_version = "unknown"

    # spaCy model SHA — use meta.json's "name+version" digest so any model
    # upgrade triggers a provenance change.
    spacy_model_sha = _spacy_model_sha(nlp)

    import hashlib

    corpus_sha = (
        hashlib.sha256(corpus_path.read_bytes()).hexdigest()
        if corpus_path.exists()
        else "missing"
    )

    provenance = IndexTreeProvenance(
        spacy_version=spacy_version,
        spacy_model_sha=spacy_model_sha,
        eyecite_version=eyecite_version,
        reporters_db_version=reporters_db_version,
        courts_db_version=courts_db_version,
        pdf_sha256=pdf_sha256,
        corpus_sha=corpus_sha,
        concepts_sha=dict(sorted(concepts_sha.items())),
        tables_sha=dict(sorted(tables_sha.items())),
        pre_dedup_count=pre_dedup_count,
        post_dedup_count=post_dedup_count,
        post_deconflict_count=post_deconflict_count,
        post_zero_evidence_count=post_zero_evidence_count,
        oversize_parent_count=oversize_parent_count,
        sub_entry_total_count=sub_entry_total_count,
        oob_status=oob_status,
        max_sub_entries_per_parent=max_sub_per_parent,
        parents_with_no_locators=parents_with_no_locators,
        dropped_table_citations=dropped_table_citations,
        zero_evidence_drops=sorted(zero_evidence_drops),
        slug_collision_count=slug_collision_count,
        iteration_depth=iteration_depth,
        frozen_timestamp=0,
    )

    tree = IndexTree(
        schema_version="1.0",
        provenance=provenance,
        entries=final_entries,
    )
    return tree, ledger_rows


def _spacy_model_sha(nlp) -> str:
    """Return a stable hash of the spaCy model identity (lang+name+version).

    Avoids hashing the entire model directory (slow) — we use the canonical
    model identity tuple per RESEARCH §H-10. Defensive fallback to "unknown"
    if meta is unavailable.
    """
    import hashlib

    try:
        meta = getattr(nlp, "meta", None) or {}
        ident = f"{meta.get('lang','')}_{meta.get('name','')}_{meta.get('version','')}"
        return hashlib.sha256(ident.encode("utf-8")).hexdigest()
    except Exception:  # pragma: no cover
        return "unknown"
