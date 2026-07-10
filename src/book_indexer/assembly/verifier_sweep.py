"""Per-canonical verify() sweep — the SOLE Phase 4 caller of verify().

Lock #1 enforcement: this module is the ONLY entry point in ``assembly/``
that imports/calls ``book_indexer.verify.verify``. Downstream stages
(cite_rule, subdivide, cross_refs, tree) operate over the Evidence lists
this module produces.

Algorithm: RESEARCH §H-4 verbatim. ProcessPoolExecutor(max_workers=8) per
empirical measurement (1100 buckets × 8 procs ≈ 20s wall-clock, comfortably
under the 30s budget). Each worker opens its own sqlite3.Connection
(Connection is not shareable across processes per the Python sqlite3 docs).
ThreadPoolExecutor is FORBIDDEN: RESEARCH §H-4 line 396-397 documents that
ThreadPool is empirically WORSE than sequential due to sqlite GIL contention.

Determinism (Lock #5): ProcessPoolExecutor.map preserves input order; the
parent sets PYTHONHASHSEED=0 + TZ=UTC + LC_ALL=C.UTF-8 (autouse fixture in
tests/conftest.py + CLI preflight in __main__.py). Workers inherit. Per-bucket
dedup is by (section_ref, folio); the kept representative is the lowest
(pdf_page, token_offset) — symbolic, deterministic. Output sorted by
(section_ref, folio) ascending (the locator key, NOT the corpus key — per
Lock #4 the printed folio is the public citation).

Lock #1 boundary: this module READS attributes off Evidence rows
(e.section_ref, e.folio, e.pdf_page, e.token_offset) but never CONSTRUCTS an
Evidence — that is verify()'s exclusive responsibility. The Lock #1
invariant test test_verify_is_sole_locator_source.py treats ``assembly/``
as a legitimate non-emitter dir (joined the exclusion list alongside
verify/, ingest/, tables/) for the same reason tables/verifier_bridge.py
does: it wraps verify() and threads Evidence through unchanged.

requirements_addressed: ASM-02 — for every (canonical, variant) pair the
pipeline runs verify() exactly once to enumerate every Evidence; only
verified occurrences become index citations.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor

from book_indexer.verify import verify
from book_indexer.verify.evidence import Evidence

from .dedup import BucketCandidate

# Type alias for the sweep output. Each canonical's lemma_key maps to its
# deduplicated, sorted Evidence list.
EvidenceByCanonical = dict[str, list[Evidence]]


def _sweep_one(args: tuple[str, list[str], str]) -> list[Evidence]:
    """Worker function: open own connection, call verify(), dedupe.

    Module-level (NOT nested) so ProcessPoolExecutor can pickle it.

    Args:
        args: ``(canonical, variants, corpus_path)``.
            canonical: chosen canonical surface for the bucket.
            variants: list of variant surfaces (acronyms, prose-form, etc.);
                if empty, falls back to ``[canonical]``.
            corpus_path: path to artifacts/page_corpus.sqlite.

    Returns:
        List of Evidence rows, deduplicated by (section_ref, folio) — lowest
        (pdf_page, token_offset) representative kept — and sorted by
        (section_ref, folio) ascending.
    """
    canonical, variants, corpus_path = args
    if not variants:
        variants = [canonical]

    # Open read-only via URI to prevent any accidental mutation in the worker.
    conn = sqlite3.connect(f"file:{corpus_path}?mode=ro", uri=True)
    try:
        # Single verify() call with variants_for=callback (RESEARCH §H-4
        # line 376: 2× faster than per-variant calls because the page walk
        # amortizes across variants).
        evs_iter = verify(canonical, conn, variants_for=lambda _: variants)

        # Dedup by (section_ref, folio); keep lowest (pdf_page, token_offset).
        seen: dict[tuple[str, str], Evidence] = {}
        for ev in evs_iter:
            key = (ev.section_ref, ev.folio)
            cur = seen.get(key)
            if cur is None or (ev.pdf_page, ev.token_offset) < (
                cur.pdf_page,
                cur.token_offset,
            ):
                seen[key] = ev

        # Sort by (section_ref, folio) ASC — the locator key for Phase 4
        # (NOT pdf_page, which is a corpus-internal ordinal; Lock #4 makes
        # the printed folio the public citation).
        return sorted(seen.values(), key=lambda e: (e.section_ref, e.folio))
    finally:
        conn.close()


def sweep_canonical(
    canonical: str,
    variants: list[str],
    corpus_path: str,
) -> list[Evidence]:
    """Single-canonical sweep — for unit tests / one-off lookups.

    For batch sweeps over many buckets, use ``run_sweep``.
    """
    return _sweep_one((canonical, variants, corpus_path))


def run_sweep(
    buckets: dict[str, BucketCandidate],
    canonical_chooser: Callable[[BucketCandidate], str],
    corpus_path: str = "artifacts/page_corpus.sqlite",
    max_workers: int = 8,
) -> EvidenceByCanonical:
    """Run verify() over every bucket; return Evidence keyed by lemma_key.

    Args:
        buckets: dict[lemma_key, BucketCandidate] from
            ``dedup.build_buckets``.
        canonical_chooser: callable returning the chosen canonical surface
            for a bucket. Typically ``canonical.elect_canonical``.
        corpus_path: path to artifacts/page_corpus.sqlite. Default
            ``artifacts/page_corpus.sqlite`` matches the cold-build CLI.
        max_workers: ProcessPoolExecutor worker count. Default 8 per
            RESEARCH §H-4 (lands at ~20s wall-clock for ~1100 buckets;
            fall back to 4 if memory-constrained).

    Returns:
        dict[lemma_key, list[Evidence]]. Keys preserved in input order
        (ProcessPoolExecutor.map preserves input order — RESEARCH §H-4 Key
        Constraints). A bucket with no body matches has an empty list.

    Raises:
        sqlite3.OperationalError: if ``corpus_path`` is not a readable
            sqlite database (propagates from the worker).
        pydantic.ValidationError / VerifierError: from verify() if a hit's
            enriched form fails Evidence validation (does NOT swallow).
    """
    if not buckets:
        return {}

    keys: list[str] = []
    args_list: list[tuple[str, list[str], str]] = []
    for lemma_key, bucket in buckets.items():
        canonical = canonical_chooser(bucket)
        # Variants for verify() = surfaces ∪ variants ∪ canonical.
        # Use a sorted set so two consecutive runs of run_sweep produce
        # byte-identical inputs to the worker pool (Lock #5).
        variants_set = set(bucket.surfaces) | set(bucket.variants) | {canonical}
        variants = sorted(variants_set)
        keys.append(lemma_key)
        args_list.append((canonical, variants, corpus_path))

    with ProcessPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_sweep_one, args_list, chunksize=10))

    return dict(zip(keys, results, strict=True))
