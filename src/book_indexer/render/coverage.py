"""AUD-03 — Coverage report extender.

Reads Phase 4's ``artifacts/coverage.draft.md`` and produces the FINAL
``artifacts/audit/coverage.md`` by:

  1. Stripping the Phase 4 ``## 6. Calibration Pointer`` section (Open
     Question 4 in RESEARCH §H-12 — researcher recommendation: REPLACE
     with Phase 5 calibration block, NOT append).
  2. Appending 9 new sections (RESEARCH §H-10):
       7.  Per-Chapter Concept Counts
       8.  Per-Section Concept Counts (top-20)
       9.  Folio-Tier Histogram (Phase 1 ``folio_resolution_audit``)
       10. Section-Tier Histogram (locator level distribution)
       11. Dropped Candidates (B-05 cruft + Phase 4 deconflict + zero-evidence)
       12. Orphan Variants (variants with no matched_variant in evidence)
       13. Synthesized Main Entries (B-06 — render-time projections,
           per Open Q3 surface ONLY here)
       14. Page-Range Collapses (D-03 summary)
       15. Render Performance (Wave 4 fills <actual> markers)
  3. Appending the new ``## Phase 5 Calibration`` section with
     ``<actual>`` placeholders that Wave 4's cold-build acceptance gate
     fills via regex pass.

Critical determinism contracts:
  - LF-only output (no CRLF).
  - All sorted iterations.
  - All counts emitted in deterministic key order.
  - No timestamps inline (the metadata block at the head of any caller-
    composed output is the single source of truth — coverage.md itself
    has no timestamp).

requirements_addressed: AUD-03.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, cast

from .ir import IndexTree
from .synthesize import SyntheticEntry

__all__ = [
    "PER_SECTION_TOPN",
    "extend_coverage_report",
]


# Top-N cap on the per-section table (long tail truncated for readability).
PER_SECTION_TOPN: int = 20


# Regex matching Phase 4's "## N. Calibration Pointer" header (any level number).
# RESEARCH §H-12 Open Q4 — REPLACE this section with Phase 5's calibration block.
_CALIBRATION_POINTER_HEADER_RE = re.compile(
    r"^##\s+\d+\.\s+Calibration\s+Pointer\s*$",
    re.MULTILINE,
)


# -----------------------------------------------------------------------------
# Phase 4 draft surgery
# -----------------------------------------------------------------------------


def _strip_phase4_calibration_pointer(draft_md: bytes) -> bytes:
    """Remove the Phase 4 ``## N. Calibration Pointer`` section.

    Per Open Q4 (RESEARCH §H-12): Phase 5 REPLACES this section with its
    own ``## Phase 5 Calibration`` block. Everything from the
    Calibration Pointer header through the end of the draft is excised
    (Phase 4 places it at the tail of the draft per
    ``artifacts/coverage.draft.md``).
    """
    text = draft_md.decode("utf-8")
    match = _CALIBRATION_POINTER_HEADER_RE.search(text)
    if not match:
        # Phase 4 didn't emit a Calibration Pointer section — pass through
        # unmodified. Forward-compat for future Phase 4 schema changes.
        return draft_md
    return text[: match.start()].rstrip("\n").encode("utf-8") + b"\n"


# -----------------------------------------------------------------------------
# Section renderers
# -----------------------------------------------------------------------------


def _chapter_key(section_ref: str) -> str:
    """Bucket a section_ref by chapter for the per-chapter histogram.

    Examples:
        '§3.02.1' → '§3'
        '§1' → '§1'
        'Chapter 1' → 'Chapter 1'
    """
    if section_ref.startswith("§"):
        return section_ref.split(".")[0]
    return section_ref


def _section_level(section_ref: str) -> int:
    """Compute the locator section depth.

    Examples:
        'Chapter 1' → 0
        '§1' → 1
        '§1.02' → 2
        '§1.02.1' → 3
    """
    if not section_ref.startswith("§"):
        return 0
    return section_ref.count(".") + 1


def _render_per_chapter_table(tree: IndexTree) -> str:
    counts: Counter[str] = Counter()
    for e in tree.entries:
        if not e.locators:
            continue
        counts[_chapter_key(e.locators[0].section_ref)] += 1

    lines = [
        "## 7. Per-Chapter Concept Counts",
        "",
        "| Chapter | Entries |",
        "|---------|---------|",
    ]
    for chapter in sorted(counts.keys()):
        lines.append(f"| {chapter} | {counts[chapter]} |")
    lines.append("")
    return "\n".join(lines)


def _render_per_section_table(tree: IndexTree, top_n: int = PER_SECTION_TOPN) -> str:
    counts: Counter[str] = Counter()
    for e in tree.entries:
        if not e.locators:
            continue
        counts[e.locators[0].section_ref] += 1

    # Sort by count desc, then section_ref asc — ties broken deterministically.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]

    lines = [
        f"## 8. Per-Section Concept Counts (top-{top_n})",
        "",
        "| Section | Entries |",
        "|---------|---------|",
    ]
    for section_ref, count in ranked:
        lines.append(f"| {section_ref} | {count} |")
    if not ranked:
        lines.append("| (none) | 0 |")
    lines.append("")
    if len(counts) > top_n:
        lines.append(f"*Long tail: {len(counts) - top_n} additional sections truncated.*")
        lines.append("")
    return "\n".join(lines)


def _render_folio_tier_histogram(corpus_path: Path) -> str:
    """Query SQLite folio_resolution_audit (final_tier) and emit a histogram."""
    uri = f"file:{Path(corpus_path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT final_tier, COUNT(*) "
            "FROM folio_resolution_audit GROUP BY final_tier "
            "ORDER BY final_tier ASC"
        ).fetchall()
    finally:
        conn.close()

    lines = [
        "## 9. Folio-Tier Histogram",
        "",
        "Phase 1 folio cascade tier distribution (per page).",
        "",
        "| Tier | Pages |",
        "|------|-------|",
    ]
    for tier, count in rows:
        lines.append(f"| {tier} | {count} |")
    if not rows:
        lines.append("| (none) | 0 |")
    lines.append("")
    return "\n".join(lines)


def _render_section_tier_histogram(tree: IndexTree) -> str:
    counts: Counter[int] = Counter()
    for e in tree.entries:
        for loc in e.locators:
            counts[_section_level(loc.section_ref)] += 1

    lines = [
        "## 10. Section-Tier Histogram",
        "",
        "Locator depth distribution. Level 0 = front-matter / chapter-only;",
        "level 1 = `§N`; level 2 = `§N.NN`; level 3 = `§N.NN.M`.",
        "",
        "| Level | Locators |",
        "|-------|----------|",
    ]
    for level in sorted(counts.keys()):
        lines.append(f"| {level} | {counts[level]} |")
    if not counts:
        lines.append("| (none) | 0 |")
    lines.append("")
    return "\n".join(lines)


def _render_dropped_candidates_table(
    b05_drops: list[str],
    phase4_provenance: dict[str, Any],
) -> str:
    deconflict = phase4_provenance.get("dropped_table_citations", []) or []
    zero_ev = phase4_provenance.get("zero_evidence_drops", []) or []

    # First three examples per source for readability.
    def _examples(items: list[Any]) -> str:
        return "; ".join(str(i) for i in items[:3]) if items else "—"

    rows = [
        ("B-05 cruft", len(b05_drops), _examples(b05_drops)),
        (
            "Phase 4 deconflict (D-04)",
            len(deconflict),
            _examples([d.get("lemma_key", str(d)) for d in deconflict]),
        ),
        ("Phase 4 zero-evidence", len(zero_ev), _examples(zero_ev)),
    ]
    total = sum(r[1] for r in rows)

    lines = [
        "## 11. Dropped Candidates",
        "",
        "| Source | Count | Examples |",
        "|--------|-------|----------|",
    ]
    for src, count, examples in rows:
        lines.append(f"| {src} | {count} | {examples} |")
    lines.append(f"| **Total** | **{total}** | |")
    lines.append("")
    return "\n".join(lines)


def _render_orphan_variants_table(
    tree: IndexTree, evidence_ledger: list[dict[str, Any]]
) -> str:
    matched: set[str] = {
        r.get("matched_variant", "") for r in evidence_ledger if r.get("matched_variant")
    }

    orphans: list[tuple[str, str]] = []  # (canonical, variant)
    for e in tree.entries:
        for v in e.variants:
            if v not in matched:
                orphans.append((e.canonical, v))

    orphans.sort()

    lines = [
        "## 12. Orphan Variants",
        "",
        "Variants declared in `entries[*].variants` with NO `matched_variant`",
        "row in the evidence ledger — typically tokenization artifacts or",
        "lemma forms that no concrete page mention attached to.",
        "",
        f"Total: **{len(orphans)}**",
        "",
        "| Canonical | Orphan Variant |",
        "|-----------|----------------|",
    ]
    for canonical, variant in orphans[:50]:  # cap at 50 for readability
        lines.append(f"| {canonical} | {variant} |")
    if not orphans:
        lines.append("| (none) | (none) |")
    elif len(orphans) > 50:
        lines.append(f"| *…and {len(orphans) - 50} more (truncated)* | |")
    lines.append("")
    return "\n".join(lines)


def _render_synthesized_table(synthetics: list[SyntheticEntry]) -> str:
    """B-06 synthesized main entries — Open Q3 surfaces ONLY here.

    NOT in index_evidence.json (that's per-Locator audit; synthesized
    entries are render-time projections without their own evidence rows).
    """
    lines = [
        "## 13. Synthesized Main Entries (B-06)",
        "",
        "B-06 render-time projections per RESEARCH §H-5 SPEC AMENDMENT",
        "(union-of-token-lemmas). These entries appear in `index.md` /",
        "`index.docx` as parent entries collecting their sibling sub-entries,",
        "but do NOT have rows in `index_tree.json` or `index_evidence.json`",
        "(per Open Question 3 — RESEARCH §H-12).",
        "",
        f"Total synthesized: **{len(synthetics)}**",
        "",
        "| Stem | Sibling Count | Locator Count |",
        "|------|---------------|---------------|",
    ]
    for s in sorted(synthetics, key=lambda x: x.stem):
        lines.append(
            f"| {s.stem} | {len(s.sibling_canonicals)} | {len(s.locators)} |"
        )
    if not synthetics:
        lines.append("| (none) | 0 | 0 |")
    lines.append("")
    return "\n".join(lines)


def _render_range_collapses_summary(total: int) -> str:
    lines = [
        "## 14. Page-Range Collapses (D-03)",
        "",
        "Contiguous-folio range collapses emitted by `range_collapse.py`.",
        "On the reference corpus this is vacuous (Phase 4 cite-rule already coalesces).",
        "",
        f"Total collapsed locators: **{total}**",
        "",
    ]
    return "\n".join(lines)


def _render_render_performance_placeholder(render_metrics: dict[str, Any]) -> str:
    """Wave 4 cold-build acceptance gate.

    Wall-clock numbers are non-deterministic across runs (Lock #5 forbids
    them in the byte-identical coverage.md output). The canonical wall-
    clock record lives in stdout telemetry / `metadata.json` runtime
    sidecars — coverage.md just records the **stages** that exist.
    """
    lines = [
        "## 15. Render Performance",
        "",
        "Wall-clock numbers are non-deterministic across runs and would",
        "break Lock #5 (byte-identical replay). They are emitted to stdout",
        "telemetry and `artifacts/audit/metadata.json` instead. coverage.md",
        "documents only the stages that exist.",
        "",
        "| Stage | Wall Clock (s) |",
        "|-------|----------------|",
        "| markdown.render_markdown | n/a (see telemetry) |",
        "| docx.render_docx | n/a (see telemetry) |",
        "| audit.build_audit_bundle | n/a (see telemetry) |",
        "| coverage.extend_coverage_report | n/a (see telemetry) |",
        "| **Total cold build** | n/a (see telemetry) |",
        "",
    ]
    return "\n".join(lines)


def _render_curator_pass_section(
    *,
    overrides: Any | None,
    dangling_xrefs_stripped: list[str],
    dropped_plural_variants: list[tuple[str, str]],
) -> str:
    """Phase 7 v1.1 — Curator Pass audit section (CONTEXT line 585).

    Lists:
      (a) removals applied (count + sample of 5 terms),
      (b) recapitalizations applied (count + sample of 5 pairs),
      (c) dangling-xref strips (count + sample of 5),
      (d) plural variants suppressed (CUR-03; count + sample of 5).

    When ``overrides`` is None (no curator pass active) the section emits a
    "no curator pass" stub so the section header always exists for grep
    discoverability.

    Determinism: only counts + sorted samples are emitted; no wall-clock
    or run-state. Lock #5 byte-identity preserved.
    """
    lines = ["## Curator Pass (Phase 7 v1.1)", ""]

    if overrides is None:
        lines.extend([
            "No curator overrides active — render path consumed `overrides=None`.",
            "(This is the v1.0 baseline; v1.1 build path always loads the",
            "curator fixture before invoking the renderers.)",
            "",
        ])
        return "\n".join(lines)

    removed = sorted(getattr(overrides, "removal_set", frozenset()))
    recap_pairs = sorted(
        cast(
            "tuple[tuple[str, str], ...]",
            getattr(overrides, "recapitalize_pairs", ()),
        ),
        key=lambda p: (p[0], p[1]),
    )
    keep_plural = sorted(
        getattr(overrides, "keep_plural_variants", []),
        key=str.lower,
    )

    # Sort + dedup the runtime accumulators for byte-determinism. The
    # accumulators may contain duplicates (one per render pass); we list
    # unique entries only.
    unique_xrefs = sorted(set(dangling_xrefs_stripped))
    unique_plural_drops = sorted(set(dropped_plural_variants))

    # (a) Removals.
    lines.extend([
        f"**Removals applied:** {len(removed)} terms",
    ])
    for t in removed[:5]:
        lines.append(f"  - {t}")
    if len(removed) > 5:
        lines.append(f"  - …and {len(removed) - 5} more")
    lines.append("")

    # (b) Recapitalizations.
    lines.extend([
        f"**Recapitalizations applied:** {len(recap_pairs)} pairs",
    ])
    for wrong, right in recap_pairs[:5]:
        lines.append(f"  - {wrong} → {right}")
    if len(recap_pairs) > 5:
        lines.append(f"  - …and {len(recap_pairs) - 5} more")
    lines.append("")

    # (c) Dangling cross-references stripped.
    lines.extend([
        f"**Dangling cross-references stripped:** {len(unique_xrefs)}",
    ])
    for t in unique_xrefs[:5]:
        lines.append(f"  - {t}")
    if len(unique_xrefs) > 5:
        lines.append(f"  - …and {len(unique_xrefs) - 5} more")
    lines.append("")

    # (d) Plural variants suppressed (CUR-03).
    lines.extend([
        f"**Plural variants suppressed (CUR-03):** {len(unique_plural_drops)}",
    ])
    for canonical, variant in unique_plural_drops[:5]:
        lines.append(f"  - {variant} suppressed from `{canonical}` entry")
    if len(unique_plural_drops) > 5:
        lines.append(f"  - …and {len(unique_plural_drops) - 5} more")
    if keep_plural:
        lines.append(
            "  keep_plural_variants in fixture: "
            + ", ".join(keep_plural)
        )
    lines.append("")

    return "\n".join(lines)


def _render_calibration_section_placeholder(
    *,
    b05_drop_count: int,
    b06_synthesize_count: int,
    range_collapses_total: int,
    entries_in_md: int,
    evidence_rows_in_audit: int,
) -> str:
    """Phase 5 calibration block per Open Q4 — REPLACES Phase 4 Calibration Pointer.

    Determinism: every value passed in is deterministic-by-construction
    from the same upstream artifacts as the rest of coverage.md, so
    embedding them keeps Lock #5's byte-identity invariant intact.
    cold_render_wall_clock_s is intentionally OMITTED from this block
    (it would break determinism); the canonical value lives in stdout
    telemetry per RESEARCH §H-12 + Plan 05-05 calibration policy.
    """
    lines = [
        "## Phase 5 Calibration",
        "",
        "Phase 5 cold-build pool-size thresholds (`test_render_pool_size.py`),",
        "calibrated by Plan 05-05 against this run's empirical yields:",
        "",
        f"- b05_drop_count: {b05_drop_count} (initial 14)",
        f"- b06_synthesize_count: {b06_synthesize_count} (initial 22)",
        f"- range_collapse_total: {range_collapses_total} "
        "(initial 0; vacuous on the reference corpus)",
        f"- entries_in_md: {entries_in_md}",
        f"- evidence_rows_in_audit: {evidence_rows_in_audit}",
        "- cold_render_wall_clock_s: n/a "
        "(non-deterministic; see stdout telemetry; initial budget 5.0s)",
        "",
    ]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def extend_coverage_report(
    draft_md: bytes,
    tree: IndexTree,
    evidence_ledger: list[dict[str, Any]],
    sections_payload: dict[str, Any],
    render_metrics: dict[str, Any],
    b05_drops: list[str],
    b06_synthetics: list[SyntheticEntry],
    range_collapses_total: int,
    corpus_path: Path,
    phase4_provenance: dict[str, Any],
    overrides: Any | None = None,
    curator_log: dict[str, list] | None = None,
) -> bytes:
    """Compose the FINAL coverage.md by extending Phase 4's draft.

    Args:
        draft_md: Phase 4's ``artifacts/coverage.draft.md`` bytes.
        tree: Phase 4's IndexTree (read-only).
        evidence_ledger: rows from ``artifacts/index_tree_evidence.json``.
        sections_payload: ``dump_sections(conn)`` parsed.
        render_metrics: per-stage wall-clock dict (Wave 4 fills).
        b05_drops: list of canonical strings dropped by `is_cruft`.
        b06_synthetics: list of `SyntheticEntry` from synthesis.
        range_collapses_total: total D-03 collapses (vacuous on
            the reference corpus).
        corpus_path: path to ``artifacts/page_corpus.sqlite`` (for the
            folio-tier histogram).
        phase4_provenance: parsed ``index_tree.provenance.json``.

    Returns:
        LF-terminated UTF-8 bytes — the FINAL coverage.md.
    """
    stripped = _strip_phase4_calibration_pointer(draft_md)

    parts: list[bytes] = [stripped]
    if not stripped.endswith(b"\n"):
        parts.append(b"\n")

    # 9 new sections — strict order per RESEARCH §H-10.
    section_renders = [
        _render_per_chapter_table(tree),
        _render_per_section_table(tree),
        _render_folio_tier_histogram(corpus_path),
        _render_section_tier_histogram(tree),
        _render_dropped_candidates_table(b05_drops, phase4_provenance),
        _render_orphan_variants_table(tree, evidence_ledger),
        _render_synthesized_table(b06_synthetics),
        _render_range_collapses_summary(range_collapses_total),
        _render_render_performance_placeholder(render_metrics),
        _render_curator_pass_section(
            overrides=overrides,
            dangling_xrefs_stripped=(
                (curator_log or {}).get("dangling_xrefs_stripped", []) if curator_log else []
            ),
            dropped_plural_variants=(
                (curator_log or {}).get("dropped_plural_variants", []) if curator_log else []
            ),
        ),
        # Replacement Calibration block per Open Q4. Values are deterministic-
        # by-construction from upstream artifacts so embedding them keeps
        # Lock #5 byte-identity intact. cold_render_wall_clock_s is OMITTED
        # because it varies across runs.
        _render_calibration_section_placeholder(
            b05_drop_count=len(b05_drops),
            b06_synthesize_count=len(b06_synthetics),
            range_collapses_total=range_collapses_total,
            entries_in_md=(
                len([e for e in tree.entries if e.canonical not in set(b05_drops)])
                + len(b06_synthetics)
            ),
            evidence_rows_in_audit=len(evidence_ledger),
        ),
    ]

    for section in section_renders:
        parts.append(section.encode("utf-8"))
        if not section.endswith("\n"):
            parts.append(b"\n")

    out = b"".join(parts)

    # Lock #5 belt-and-braces: strip any CR bytes that sneaked in.
    out = out.replace(b"\r", b"")
    return out
