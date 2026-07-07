"""ASM-07 size-band check + draft coverage report (D-08).

Phase 4 emits a DRAFT coverage report (``artifacts/coverage.draft.md``)
that Phase 5 finalizes. The draft answers two questions:

  1. Is the index size within the ASM-07 800–1500 band? (``compute_oob_status``)
  2. What does the post-pipeline attrition look like? (counts from provenance)

Per D-08: an out-of-band size is FLAGGED but does NOT hard-fail the
build. The Phase 5 sampled-review gate (QUAL-02) escalates to full review
when ``oob_status != "none"``.

Public API:
    ASM07_MIN, ASM07_MAX
    compute_oob_status(entry_count) -> Literal["none", "under", "over"]
    emit_draft_report(provenance_dict, out_path)

requirements_addressed: ASM-07.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

# ASM-07 hard band — RESEARCH §H-8. The hard floor of 800 and ceiling of
# 1500 are NEVER lowered/raised without an ASM amendment in REQUIREMENTS.md.
ASM07_MIN: int = 800
ASM07_MAX: int = 1500


def compute_oob_status(entry_count: int) -> Literal["none", "under", "over"]:
    """Classify a tree's entry count against the ASM-07 band.

    Returns:
        ``"under"`` if ``entry_count < ASM07_MIN`` (e.g., 799),
        ``"over"`` if ``entry_count > ASM07_MAX`` (e.g., 1501),
        ``"none"`` otherwise (within the inclusive band 800..1500).
    """
    if entry_count < ASM07_MIN:
        return "under"
    if entry_count > ASM07_MAX:
        return "over"
    return "none"


def emit_draft_report(provenance: dict[str, Any], out_path: Path) -> None:
    """Write a draft coverage Markdown report.

    Six sections (D-08):
      1. Pool Size — entries vs. ASM-07 band.
      2. ASM-07 Band Check — explicit oob_status + flag.
      3. Subdivide Stats — oversize parents, sub-entry totals.
      4. Attrition Funnel — pre_dedup → post_zero_evidence counts.
      5. Notes — slug collisions, dropped table citations, zero-evidence drops.
      6. Calibration Pointer — note that Phase 5 finalizes this report.

    Args:
        provenance: a dict from ``IndexTreeProvenance.model_dump(mode="json")``
            (or any superset). Missing keys default to safe sentinels.
        out_path: target Markdown path. Parent directory created if absent.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    pre = provenance.get("pre_dedup_count", 0)
    post_dedup = provenance.get("post_dedup_count", 0)
    post_deconflict = provenance.get("post_deconflict_count", 0)
    post_zero = provenance.get("post_zero_evidence_count", 0)
    oversize_parents = provenance.get("oversize_parent_count", 0)
    sub_total = provenance.get("sub_entry_total_count", 0)
    max_sub = provenance.get("max_sub_entries_per_parent", 0)
    iter_depth = provenance.get("iteration_depth", 0)
    parents_no_locs = provenance.get("parents_with_no_locators", 0)
    slug_coll = provenance.get("slug_collision_count", 0)
    zero_drops = provenance.get("zero_evidence_drops", [])
    dropped_tables = provenance.get("dropped_table_citations", [])
    oob = provenance.get("oob_status", "none")

    # Final entry count = post_zero_evidence_count (the surviving canonicals).
    n_entries = post_zero
    band_str = (
        f"{ASM07_MIN} ≤ {n_entries} ≤ {ASM07_MAX}"
        if oob == "none"
        else f"{n_entries} (FLAGGED: {oob} — {ASM07_MIN}..{ASM07_MAX})"
    )

    lines = [
        "# Coverage Report (DRAFT)",
        "",
        "Phase 4 emits this draft; Phase 5 finalizes it. Do NOT edit this",
        "file by hand — it is regenerated on every cold build.",
        "",
        "## 1. Pool Size",
        "",
        f"- Surviving index entries: **{n_entries}**",
        f"- ASM-07 target band: **{ASM07_MIN}–{ASM07_MAX}**",
        f"- Status: **{oob}**",
        "",
        "## 2. ASM-07 Band Check",
        "",
        f"- Range: {band_str}",
        f"- oob_status: `{oob}`",
        ("- ✓ Within target band — sampled-review gate proceeds normally."
         if oob == "none"
         else "- ⚠ FLAGGED — Phase 5 sampled-review gate escalates to full review."),
        "",
        "## 3. Subdivide Stats (D-04 / ASM-04)",
        "",
        f"- Oversize parents (>7 locators, subdivided): **{oversize_parents}**",
        f"- Total sub-entries emitted: **{sub_total}**",
        f"- Max sub-entries per parent: **{max_sub}**",
        f"- Iteration depth used: **{iter_depth}**",
        f"- Parents with no locators (should be 0): **{parents_no_locs}**",
        "",
        "## 4. Attrition Funnel",
        "",
        "| Stage | Count |",
        "|---|---|",
        f"| pre-dedup candidates | {pre} |",
        f"| post-dedup buckets | {post_dedup} |",
        f"| post-deconflict (D-04) | {post_deconflict} |",
        f"| post-zero-evidence drop | {post_zero} |",
        "",
        "## 5. Notes",
        "",
        f"- Slug collisions resolved with -2/-3 suffixes: **{slug_coll}**",
        f"- Buckets dropped as table-citations (D-04): **{len(dropped_tables)}**",
        f"- Buckets dropped for zero verify() evidence: **{len(zero_drops)}**",
        "",
        "## 6. Calibration Pointer",
        "",
        "Phase 5 (Plan 04-05 cold-build acceptance gate) calibrates the",
        "size-band thresholds in `tests/integration/test_index_tree_pool_size.py`",
        "against this empirical yield. Initial estimates from RESEARCH §H-12",
        "are PRESERVED in the test file's calibration block until Phase 5",
        "edits them per the 15%-headroom policy.",
        "",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
