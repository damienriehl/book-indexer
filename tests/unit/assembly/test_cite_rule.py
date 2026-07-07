"""Unit tests for ``book_indexer.assembly.cite_rule`` helpers.

Covers:
  * ``lowest_common_ancestor`` — singleton (D-02 no-promote), sibling-promote,
    span-majors → chapter, mixed-depth-truncation, empty defensive.
  * ``group_by_chapter`` — chapter integer extraction + malformed-path
    defensive bucket.

The 11 RESEARCH §H-3 truth-table fixtures are exercised in the integration
test ``tests/integration/test_hybrid_deepest_section_rule.py``.

Lock #1: this module never constructs ``Evidence`` directly — uses the
``make_evidence`` factory from ``tests/unit/assembly/conftest.py``.

requirements_addressed: ASM-03 (lock the LCA helper).
"""
from __future__ import annotations

from book_indexer.assembly.cite_rule import (
    group_by_chapter,
    lowest_common_ancestor,
)


# ---------------------------------------------------------------------------
# lowest_common_ancestor
# ---------------------------------------------------------------------------


class TestLowestCommonAncestor:
    """RESEARCH §H-3 LCA helper — pure tuple-of-strings input."""

    def test_singleton_returns_deepest(self):
        """D-02: singleton at depth → cite at deepest, NO auto-promote."""
        assert (
            lowest_common_ancestor([("§2", "§2.04", "§2.04.5")]) == "§2.04.5"
        )

    def test_sibling_subsections_promote_to_major(self):
        result = lowest_common_ancestor(
            [
                ("§2", "§2.04", "§2.04.1"),
                ("§2", "§2.04", "§2.04.2"),
            ]
        )
        assert result == "§2.04"

    def test_span_majors_promote_to_chapter(self):
        result = lowest_common_ancestor(
            [
                ("§2", "§2.04", "§2.04.1"),
                ("§2", "§2.05", "§2.05.1"),
            ]
        )
        assert result == "§2"

    def test_mixed_depth_zip_truncates_to_shortest(self):
        """Case 8 — when one path is shorter, zip truncates → LCA is §2.04."""
        result = lowest_common_ancestor(
            [
                ("§2", "§2.04", "§2.04.5"),
                ("§2", "§2.04"),
            ]
        )
        assert result == "§2.04"

    def test_empty_defensive(self):
        assert lowest_common_ancestor([]) == ""

    def test_three_identical_paths(self):
        """All-same paths return the deepest common element."""
        path = ("§2", "§2.04", "§2.04.1")
        assert lowest_common_ancestor([path, path, path]) == "§2.04.1"

    def test_disjoint_chapters_returns_first_chapter(self):
        """Defensive: cite_for_canonical groups by chapter so the LCA across
        chapters never runs in production. But if it does, the function
        returns the first path's chapter (the empty-common branch)."""
        result = lowest_common_ancestor(
            [
                ("§2", "§2.04", "§2.04.1"),
                ("§4", "§4.01", "§4.01.1"),
            ]
        )
        assert result == "§2"

    def test_singleton_at_chapter_level(self):
        """Singleton single-element path returns itself."""
        assert lowest_common_ancestor([("§3",)]) == "§3"


# ---------------------------------------------------------------------------
# group_by_chapter
# ---------------------------------------------------------------------------


class TestGroupByChapter:
    """Group Evidence by integer chapter (parsed from ``section_path[0]``)."""

    def test_groups_by_chapter_int(self, make_evidence):
        ev_a = make_evidence(("§2", "§2.04", "§2.04.1"))
        ev_b = make_evidence(("§4", "§4.01", "§4.01.1"))
        groups = group_by_chapter([ev_a, ev_b])
        assert set(groups.keys()) == {2, 4}
        assert groups[2] == [ev_a]
        assert groups[4] == [ev_b]

    def test_multiple_in_one_chapter(self, make_evidence):
        ev1 = make_evidence(("§2", "§2.04", "§2.04.1"))
        ev2 = make_evidence(("§2", "§2.05", "§2.05.1"))
        groups = group_by_chapter([ev1, ev2])
        assert set(groups.keys()) == {2}
        assert groups[2] == [ev1, ev2]

    def test_empty_input(self):
        assert group_by_chapter([]) == {}

    def test_chapter_level_evidence(self, make_evidence):
        """Level-1 (chapter-only) Evidence still groups correctly."""
        ev = make_evidence(("§3",))
        groups = group_by_chapter([ev])
        assert groups == {3: [ev]}
