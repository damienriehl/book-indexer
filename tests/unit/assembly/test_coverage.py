"""Unit tests for ``book_indexer.assembly.coverage``.

ASM-07 size-band check + draft coverage report (D-08).

requirements_addressed: ASM-07.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from book_indexer.assembly.coverage import (
    ASM07_MAX,
    ASM07_MIN,
    compute_oob_status,
    emit_draft_report,
)


# ---------------------------------------------------------------------------
# compute_oob_status — boundary tests.
# ---------------------------------------------------------------------------


def test_oob_status_below_min_is_under() -> None:
    """799 entries → under (one short of the floor)."""
    assert compute_oob_status(799) == "under"


def test_oob_status_at_min_is_none() -> None:
    """800 entries → none (the inclusive floor)."""
    assert compute_oob_status(800) == "none"


def test_oob_status_at_max_is_none() -> None:
    """1500 entries → none (the inclusive ceiling)."""
    assert compute_oob_status(1500) == "none"


def test_oob_status_above_max_is_over() -> None:
    """1501 entries → over (one above the ceiling)."""
    assert compute_oob_status(1501) == "over"


def test_oob_status_constants_are_locked() -> None:
    """The 800/1500 band is locked by REQUIREMENTS.md ASM-07."""
    assert ASM07_MIN == 800
    assert ASM07_MAX == 1500


# ---------------------------------------------------------------------------
# emit_draft_report — markdown sections present.
# ---------------------------------------------------------------------------


def test_emit_draft_report_writes_six_sections(tmp_path: Path) -> None:
    """Draft report contains the 6 documented sections."""
    out = tmp_path / "coverage.draft.md"
    provenance = {
        "pre_dedup_count": 2009,
        "post_dedup_count": 1110,
        "post_deconflict_count": 1100,
        "post_zero_evidence_count": 870,
        "oversize_parent_count": 6,
        "sub_entry_total_count": 24,
        "max_sub_entries_per_parent": 5,
        "iteration_depth": 1,
        "parents_with_no_locators": 0,
        "slug_collision_count": 3,
        "zero_evidence_drops": ["a", "b", "c"],
        "dropped_table_citations": [],
        "oob_status": "none",
    }
    emit_draft_report(provenance, out)
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # 6 numbered sections
    for header in (
        "## 1. Pool Size",
        "## 2. ASM-07 Band Check",
        "## 3. Subdivide Stats",
        "## 4. Attrition Funnel",
        "## 5. Notes",
        "## 6. Calibration Pointer",
    ):
        assert header in text, f"missing section: {header!r}"
    # Substantive counts present
    assert "**870**" in text  # post_zero_evidence_count
    assert "2009" in text  # pre_dedup
    assert "1110" in text  # post_dedup


def test_emit_draft_report_flags_oob(tmp_path: Path) -> None:
    """When oob_status != 'none', the report flags the size."""
    out = tmp_path / "coverage.draft.md"
    provenance = {
        "pre_dedup_count": 100,
        "post_dedup_count": 80,
        "post_deconflict_count": 75,
        "post_zero_evidence_count": 50,  # under the ASM-07 floor
        "oversize_parent_count": 0,
        "sub_entry_total_count": 0,
        "max_sub_entries_per_parent": 0,
        "iteration_depth": 0,
        "parents_with_no_locators": 0,
        "slug_collision_count": 0,
        "zero_evidence_drops": [],
        "dropped_table_citations": [],
        "oob_status": "under",
    }
    emit_draft_report(provenance, out)
    text = out.read_text(encoding="utf-8")
    assert "FLAGGED" in text
    assert "under" in text
    assert "sampled-review gate escalates" in text


def test_emit_draft_report_handles_empty_provenance(tmp_path: Path) -> None:
    """All keys are optional — missing keys default to safe sentinels."""
    out = tmp_path / "coverage.draft.md"
    emit_draft_report({}, out)
    text = out.read_text(encoding="utf-8")
    # Default oob_status is 'none' → not flagged
    assert "## 1. Pool Size" in text
    # 0 entries with oob_status='none' default still renders (no crash)
    assert "0" in text
