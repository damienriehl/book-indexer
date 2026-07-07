"""Unit tests for ``book_indexer.assembly.subdivide`` (ASM-04).

Coverage map:
  Behaviors (D-05 + RESEARCH §H-7):
    * suggested_subentries preferred (truncated to N=5).
    * pad with top-N noun-phrase co-occurrence when suggested < N.
    * fewer-than-N qualify → ship whatever does (no padding-with-zero).
    * ≥2-distinct-subsection threshold filter (stricter than See-also's 3).
    * sub-entries sorted alphabetically at emit (deterministic).
    * SubEntry.locators = parent_secs ∩ candidate_secs (intersection).
    * residual = parent locators NOT covered by any sub-entry.
    * iteration depth ≤ 2 with N=3 secondary pass.
    * OversizeAfterIterationError when residual >7 after iter depth 2.
    * compute_co_occurrence helper.
    * empty noun_phrase_pool returns ([], parent_locators) when
      suggested_subentries is empty (no padding possible).
    * canonical_id excluded from its own sub-entry pool.
    * NOT-oversize parent (≤7 locators) returns ([], parent_locators).

  Edge cases (RESEARCH §H-7):
    * suggested_subentries empty.
    * suggested_subentries shorter than N.
    * pool exists but no candidate qualifies (≥2 threshold).
    * still oversize after iter pass 1 → triggers iter pass 2.
    * still oversize after iter pass 2 → raises.

  Lock #1: this module never constructs Evidence (uses Locator only).

requirements_addressed: ASM-04 (subdivide oversize parents per D-05).
"""
from __future__ import annotations

import copy

import pytest

from book_indexer.assembly.errors import OversizeAfterIterationError
from book_indexer.assembly.subdivide import (
    compute_co_occurrence,
    subdivide_oversize,
)


# ---------------------------------------------------------------------------
# compute_co_occurrence
# ---------------------------------------------------------------------------


class TestComputeCoOccurrence:
    def test_full_overlap(self, make_locator):
        parent_secs = {"§2.04.1", "§2.04.2"}
        cand = [make_locator(section_ref="§2.04.1"), make_locator(section_ref="§2.04.2")]
        assert compute_co_occurrence(parent_secs, cand) == 2

    def test_partial_overlap(self, make_locator):
        parent_secs = {"§2.04.1", "§2.04.2", "§2.04.3"}
        cand = [make_locator(section_ref="§2.04.1"), make_locator(section_ref="§5.01")]
        assert compute_co_occurrence(parent_secs, cand) == 1

    def test_no_overlap(self, make_locator):
        parent_secs = {"§2.04.1"}
        cand = [make_locator(section_ref="§5.01")]
        assert compute_co_occurrence(parent_secs, cand) == 0

    def test_dedupes_section_ref(self, make_locator):
        """Multiple locators on the same section_ref count once."""
        parent_secs = {"§2.04.1"}
        cand = [
            make_locator(section_ref="§2.04.1", folio="80"),
            make_locator(section_ref="§2.04.1", folio="81"),
            make_locator(section_ref="§2.04.1", folio="82"),
        ]
        assert compute_co_occurrence(parent_secs, cand) == 1


# ---------------------------------------------------------------------------
# subdivide_oversize — happy paths
# ---------------------------------------------------------------------------


def _parent_locators_8_across_4_secs(make_locator):
    """8 locators distributed across 4 distinct sub-sections."""
    return [
        make_locator(section_ref="§2.04.1", folio="80", evidence_id=1),
        make_locator(section_ref="§2.04.1", folio="81", evidence_id=2),
        make_locator(section_ref="§2.04.2", folio="82", evidence_id=3),
        make_locator(section_ref="§2.04.2", folio="83", evidence_id=4),
        make_locator(section_ref="§2.04.3", folio="84", evidence_id=5),
        make_locator(section_ref="§2.04.3", folio="85", evidence_id=6),
        make_locator(section_ref="§2.04.4", folio="86", evidence_id=7),
        make_locator(section_ref="§2.04.4", folio="87", evidence_id=8),
    ]


class TestSubdivideOversize:
    def test_not_oversize_returns_empty(self, make_locator):
        """Parent with ≤7 locators returns ([], parent_locators)."""
        locs = [make_locator(section_ref=f"§2.04.{i}") for i in range(1, 8)]
        sub_entries, residual = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=locs,
            suggested_subentries=[],
            noun_phrase_pool={},
        )
        assert sub_entries == []
        assert residual == locs

    def test_suggested_subentries_preferred(self, make_locator):
        """Two suggested + 3 padded; sub-entries shipped alphabetically."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "physical evidence": [
                make_locator(section_ref="§2.04.1", folio="80", evidence_id=10),
                make_locator(section_ref="§2.04.2", folio="82", evidence_id=11),
            ],
            "documentary evidence": [
                make_locator(section_ref="§2.04.3", folio="84", evidence_id=12),
                make_locator(section_ref="§2.04.4", folio="86", evidence_id=13),
            ],
        }
        sub_entries, residual = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=["physical evidence", "documentary evidence"],
            noun_phrase_pool=pool,
        )
        # Sorted alphabetically by sort_key
        assert [e.text for e in sub_entries] == [
            "documentary evidence",
            "physical evidence",
        ]
        # Together they cover all 4 parent sub-sections → residual empty
        assert residual == []

    def test_only_noun_phrase_pool(self, make_locator):
        """suggested empty → all 5 from noun-phrase top-N."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "alpha-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
                make_locator(section_ref="§2.04.3"),
            ],
            "beta-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.4"),
            ],
        }
        sub_entries, residual = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        # Both qualify (≥2 distinct sub-sections shared)
        assert {e.text for e in sub_entries} == {"alpha-np", "beta-np"}

    def test_below_threshold_excluded(self, make_locator):
        """Candidate sharing only 1 sub-section is below ≥2 threshold.

        The 8-locator parent (across 4 sub-sections) cannot be reduced
        below the ≤7 threshold because the only pool candidate shares
        just 1 sub-section. D-05 + RESEARCH §H-13: iteration depth 2
        exhausted → OversizeAfterIterationError.
        """
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "alpha-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§5.01"),  # not in parent
            ],
        }
        with pytest.raises(OversizeAfterIterationError):
            subdivide_oversize(
                canonical_id="evidence",
                parent_locators=parent,
                suggested_subentries=[],
                noun_phrase_pool=pool,
            )

    def test_canonical_excluded_from_own_pool(self, make_locator):
        """The canonical id itself is never considered as its own sub-entry."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "evidence": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
                make_locator(section_ref="§2.04.3"),
            ],
            "alpha-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
            ],
        }
        sub_entries, _ = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        assert "evidence" not in {e.text for e in sub_entries}

    def test_residual_is_uncovered_locators(self, make_locator):
        """Residual = parent locators whose section_ref isn't covered."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "alpha-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
            ],
        }
        sub_entries, residual = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        # alpha-np covers §2.04.1 and §2.04.2; residual is from §2.04.3, §2.04.4
        residual_secs = {loc.section_ref for loc in residual}
        assert residual_secs == {"§2.04.3", "§2.04.4"}

    def test_subentry_locators_intersect_parent_secs(self, make_locator):
        """SubEntry.locators only carries section_refs present in parent."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "alpha-np": [
                make_locator(section_ref="§2.04.1", folio="80", evidence_id=20),
                make_locator(section_ref="§2.04.2", folio="82", evidence_id=21),
                # This locator is at a section NOT in parent — should be filtered out
                make_locator(section_ref="§5.01.1", folio="200", evidence_id=22),
            ],
        }
        sub_entries, _ = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        assert len(sub_entries) == 1
        sub_secs = {loc.section_ref for loc in sub_entries[0].locators}
        assert sub_secs == {"§2.04.1", "§2.04.2"}  # §5.01.1 filtered out

    def test_sub_entries_sorted_alphabetically(self, make_locator):
        """Output order is alphabetical regardless of insertion / co-count."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "zeta-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
                make_locator(section_ref="§2.04.3"),
            ],
            "alpha-np": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
            ],
        }
        sub_entries, _ = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        texts = [e.text for e in sub_entries]
        assert texts == sorted(texts)

    def test_truncates_suggested_to_n(self, make_locator):
        """suggested_subentries longer than N=5 is truncated."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            f"sug-{i}": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
            ]
            for i in range(1, 8)  # 7 candidates
        }
        sub_entries, _ = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[f"sug-{i}" for i in range(1, 8)],
            noun_phrase_pool=pool,
            n=5,
        )
        assert len(sub_entries) <= 5

    def test_empty_pool_oversize_raises(self, make_locator):
        """Empty pool + empty suggestions on oversize parent: no padding
        possible → iteration exhausts → OversizeAfterIterationError
        (D-05 + RESEARCH §H-13 — caller is responsible for surfacing
        this as a build-time blocker)."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        with pytest.raises(OversizeAfterIterationError):
            subdivide_oversize(
                canonical_id="evidence",
                parent_locators=parent,
                suggested_subentries=[],
                noun_phrase_pool={},
            )


# ---------------------------------------------------------------------------
# Iteration depth + OversizeAfterIterationError
# ---------------------------------------------------------------------------


class TestIteration:
    def test_iter_pass_2_resolves_residual(self, make_locator):
        """After pass 1 still >7 → pass 2 with N=3 picks more."""
        # 16 locators across 8 distinct sub-sections; pass 1 covers 4,
        # leaves 8 residual; pass 2 with N=3 picks 3 more covering 6/8;
        # residual = 2 (≤7) → OK.
        parent = []
        for i in range(1, 9):
            parent.append(make_locator(section_ref=f"§2.04.{i}", folio=str(70 + i), evidence_id=i))
            parent.append(
                make_locator(
                    section_ref=f"§2.04.{i}", folio=str(80 + i), evidence_id=100 + i
                )
            )

        # Pool has many qualifying candidates; pass 1 picks top 5,
        # pass 2 picks 3 more.
        pool = {}
        for j in range(1, 9):
            pool[f"np-{j:02d}"] = [
                make_locator(section_ref=f"§2.04.{j}"),
                make_locator(section_ref=f"§2.04.{(j % 8) + 1}"),
            ]

        sub_entries, residual = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        assert len(residual) <= 7
        assert len(sub_entries) >= 1

    def test_oversize_after_iter_raises(self, make_locator):
        """Residual still >7 after iter depth 2 → OversizeAfterIterationError."""
        # 16 locators across 16 distinct sub-sections, NO candidates that
        # share enough — every sub-entry covers only 1 sub-section so
        # ≥2 threshold filters everything out → residual stays = 16.
        parent = [
            make_locator(section_ref=f"§2.04.{i}", folio=str(50 + i), evidence_id=i)
            for i in range(1, 17)
        ]
        # No qualifying pool entries (each covers only 1 of parent's secs)
        pool = {
            f"np-{j}": [make_locator(section_ref=f"§2.04.{j}")]
            for j in range(1, 17)
        }
        with pytest.raises(OversizeAfterIterationError) as exc:
            subdivide_oversize(
                canonical_id="evidence",
                parent_locators=parent,
                suggested_subentries=[],
                noun_phrase_pool=pool,
            )
        assert "evidence" in str(exc.value)


# ---------------------------------------------------------------------------
# Defensive — input immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_does_not_mutate_inputs(self, make_locator):
        """Pure function: deepcopy inputs, run, assert unchanged."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        suggested = ["alpha"]
        pool = {
            "alpha": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
            ],
        }

        parent_snap = copy.deepcopy(parent)
        suggested_snap = copy.deepcopy(suggested)
        pool_snap = copy.deepcopy(pool)

        subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=suggested,
            noun_phrase_pool=pool,
        )

        assert parent == parent_snap
        assert suggested == suggested_snap
        assert pool == pool_snap


# ---------------------------------------------------------------------------
# RESEARCH §H-7 edge cases (5)
# ---------------------------------------------------------------------------


class TestResearchH7EdgeCases:
    def test_edge_1_suggested_empty(self, make_locator):
        """Edge 1: suggested_subentries == [] forces full noun-phrase fill."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "np-1": [
                make_locator(section_ref="§2.04.1"),
                make_locator(section_ref="§2.04.2"),
            ],
        }
        sub_entries, _ = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        assert len(sub_entries) == 1
        assert sub_entries[0].text == "np-1"

    def test_edge_2_suggested_short_of_n(self, make_locator):
        """Edge 2: suggested has 2; pad with up to 3 from noun-phrase pool."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "sug-1": [make_locator(section_ref="§2.04.1"), make_locator(section_ref="§2.04.2")],
            "sug-2": [make_locator(section_ref="§2.04.3"), make_locator(section_ref="§2.04.4")],
            "np-1": [make_locator(section_ref="§2.04.1"), make_locator(section_ref="§2.04.3")],
        }
        sub_entries, _ = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=["sug-1", "sug-2"],
            noun_phrase_pool=pool,
        )
        # All three (sug-1, sug-2, np-1) qualify → 3 sub-entries
        assert {e.text for e in sub_entries} == {"sug-1", "sug-2", "np-1"}

    def test_edge_3_no_qualifying_candidates(self, make_locator):
        """Edge 3: pool exists, but none meets ≥2-distinct-subsection
        threshold → iteration exhausts → OversizeAfterIterationError."""
        parent = _parent_locators_8_across_4_secs(make_locator)
        pool = {
            "np-1": [make_locator(section_ref="§2.04.1")],  # only 1 shared sub-sec
            "np-2": [make_locator(section_ref="§2.04.2")],
        }
        with pytest.raises(OversizeAfterIterationError):
            subdivide_oversize(
                canonical_id="evidence",
                parent_locators=parent,
                suggested_subentries=[],
                noun_phrase_pool=pool,
            )

    def test_edge_4_iter_pass_2_succeeds(self, make_locator):
        """Edge 4: iter pass 2 (N=3) brings residual ≤ 7 (covered above)."""
        # Reuse the iter test
        parent = []
        for i in range(1, 9):
            parent.append(make_locator(section_ref=f"§2.04.{i}", folio=str(70 + i), evidence_id=i))
            parent.append(
                make_locator(section_ref=f"§2.04.{i}", folio=str(80 + i), evidence_id=100 + i)
            )
        pool = {}
        for j in range(1, 9):
            pool[f"np-{j:02d}"] = [
                make_locator(section_ref=f"§2.04.{j}"),
                make_locator(section_ref=f"§2.04.{(j % 8) + 1}"),
            ]
        sub_entries, residual = subdivide_oversize(
            canonical_id="evidence",
            parent_locators=parent,
            suggested_subentries=[],
            noun_phrase_pool=pool,
        )
        assert len(residual) <= 7

    def test_edge_5_oversize_raises(self, make_locator):
        """Edge 5: still oversize after iter pass 2 → exception (covered above)."""
        parent = [
            make_locator(section_ref=f"§2.04.{i}", folio=str(50 + i), evidence_id=i)
            for i in range(1, 17)
        ]
        pool = {
            f"np-{j}": [make_locator(section_ref=f"§2.04.{j}")] for j in range(1, 17)
        }
        with pytest.raises(OversizeAfterIterationError):
            subdivide_oversize(
                canonical_id="evidence",
                parent_locators=parent,
                suggested_subentries=[],
                noun_phrase_pool=pool,
            )
