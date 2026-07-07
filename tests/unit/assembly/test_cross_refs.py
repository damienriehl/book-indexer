"""Unit tests for ``book_indexer.assembly.cross_refs`` (ASM-05 + ASM-06).

Coverage map:
  build_see_edges (mechanical, no threshold):
    * Variant → canonical mapping for slugified variants.
    * Variant equal to canonical id is skipped (no self-See).
    * Multiple variants of the same canonical all map.
    * Two canonicals with the same slugified variant collide → both
      listed (rare edge).

  build_see_also_edges (≥3 distinct sub-sections, bounded out-degree=5):
    * Two canonicals co-occurring in 3 sub-sections create symmetric
      edges.
    * Pair with co-count == 2 is below threshold (excluded).
    * Bounded out-degree: a canonical with 7 candidate edges has only 5
      in the output (top-5 by co-count, alphabetical tiebreak).
    * Output lists are alphabetically sorted (D-07 sort policy).

  Graph validation (find_cycle / find_dangling / validate_graph):
    * find_cycle returns (False, None) on DAG.
    * find_cycle returns (True, [path]) on a 3-node cycle.
    * find_dangling returns ``(source, edge_type, target)`` for missing
      ids in see and see_also.
    * validate_graph raises CycleDetectedError on a `see` cycle.
    * validate_graph raises DanglingRefError on a missing see / see_also
      target.
    * validate_graph does NOT raise for see_also "cycles" (it's
      undirected; A→B and B→A is the same edge).
    * validate_graph raises AssertionError for see_also out-degree > 5.

requirements_addressed: ASM-05 (See / See also), ASM-06 (graph validation).
"""
from __future__ import annotations

import pytest

from book_indexer.assembly.cross_refs import (
    _slugify,
    build_see_also_edges,
    build_see_edges,
    check_out_degree,
    find_cycle,
    find_dangling,
    validate_graph,
)
from book_indexer.assembly.errors import CycleDetectedError, DanglingRefError


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic_lowercases(self):
        assert _slugify("Voir Dire") == "voir-dire"

    def test_strips_punctuation(self):
        assert _slugify("FRCP 12(b)(6)") == "frcp-12-b-6"

    def test_strips_leading_trailing_hyphens(self):
        assert _slugify("- foo -") == "foo"

    def test_empty_returns_placeholder(self):
        assert _slugify("") == "x"

    def test_matches_index_entry_id_pattern(self):
        """Slug output must match the IndexEntry.id regex."""
        import re

        pattern = re.compile(r"^[a-z0-9][-a-z0-9]*(-\d+)?$")
        cases = ["Voir Dire", "FRCP 12(b)(6)", "the U.S.A.", "hearsay rule"]
        for case in cases:
            slug = _slugify(case)
            assert pattern.match(slug), f"slug {slug!r} for {case!r} fails pattern"


# ---------------------------------------------------------------------------
# build_see_edges (variant → canonical, mechanical)
# ---------------------------------------------------------------------------


class TestBuildSeeEdges:
    def test_basic_variant_mapping(self):
        canonicals = {
            "voir-dire": ["voir dire", "the voir dire"],
            "frcp-12": ["FRCP 12", "Federal Rule of Civil Procedure 12"],
        }
        edges = build_see_edges(canonicals)
        # Variant whose slug equals canonical id is skipped (no self-See).
        assert "voir-dire" not in edges
        # Variant slugs that don't equal canonical id map to that canonical.
        assert edges["the-voir-dire"] == ["voir-dire"]
        # FRCP 12 slugifies to frcp-12 == canonical → skipped.
        assert "frcp-12" not in edges
        assert edges["federal-rule-of-civil-procedure-12"] == ["frcp-12"]

    def test_variant_equal_to_canonical_skipped(self):
        """A variant whose slug equals the canonical id makes no See edge."""
        canonicals = {"voir-dire": ["voir dire", "voir-dire"]}
        edges = build_see_edges(canonicals)
        # "voir-dire" (the variant whose slug equals the id) is skipped
        assert "voir-dire" not in edges  # no self-See

    def test_two_canonicals_share_variant_slug(self):
        """Two canonicals with the same slugified variant collide → both."""
        canonicals = {
            "alpha": ["foo bar"],
            "beta": ["foo-bar"],
        }
        edges = build_see_edges(canonicals)
        assert sorted(edges["foo-bar"]) == ["alpha", "beta"]

    def test_empty_input(self):
        assert build_see_edges({}) == {}

    def test_idempotent_variant_dedupes(self):
        """Listing the same variant twice produces a single See edge."""
        canonicals = {"voir-dire": ["the voir dire", "the voir dire"]}
        edges = build_see_edges(canonicals)
        # Single edge despite duplicate variant.
        assert edges == {"the-voir-dire": ["voir-dire"]}


# ---------------------------------------------------------------------------
# build_see_also_edges (≥3 distinct sub-sections; bounded out-degree=5)
# ---------------------------------------------------------------------------


def _entry(make_index_entry, make_locator, id_, secs):
    """Build an IndexEntry with locators at the given section_refs."""
    locators = [
        make_locator(section_ref=s, folio=str(i + 50), evidence_id=i + 1)
        for i, s in enumerate(secs)
    ]
    return make_index_entry(
        id=id_, canonical=id_.replace("-", " "), sort_key=id_, locators=locators
    )


class TestBuildSeeAlsoEdges:
    def test_pair_above_threshold_creates_symmetric_edge(
        self, make_index_entry, make_locator
    ):
        a = _entry(
            make_index_entry,
            make_locator,
            "alpha",
            ["§2.04.1", "§2.04.2", "§2.04.3"],
        )
        b = _entry(
            make_index_entry,
            make_locator,
            "beta",
            ["§2.04.1", "§2.04.2", "§2.04.3"],
        )
        canonicals = {a.id: a, b.id: b}
        edges = build_see_also_edges(canonicals)
        assert edges["alpha"] == ["beta"]
        assert edges["beta"] == ["alpha"]

    def test_pair_below_threshold_excluded(
        self, make_index_entry, make_locator
    ):
        """Co-count == 2 is below threshold of 3 — no edge."""
        a = _entry(
            make_index_entry,
            make_locator,
            "alpha",
            ["§2.04.1", "§2.04.2", "§2.04.3"],
        )
        b = _entry(
            make_index_entry,
            make_locator,
            "beta",
            ["§2.04.1", "§2.04.2", "§5.01"],
        )
        canonicals = {a.id: a, b.id: b}
        edges = build_see_also_edges(canonicals)
        assert edges == {}

    def test_bounded_out_degree_5(self, make_index_entry, make_locator):
        """A canonical with 7 candidate edges has only 5 in output."""
        # alpha co-occurs with 7 partners in §2.04.1..§2.04.5 (5 secs).
        # All 7 partners share the same 5 sub-sections with alpha
        # (co-count = 5, all above threshold). Out-degree should be 5.
        common_secs = ["§2.04.1", "§2.04.2", "§2.04.3", "§2.04.4", "§2.04.5"]
        a = _entry(make_index_entry, make_locator, "alpha", common_secs)
        partners = {}
        for i in range(7):
            pid = f"partner-{i:02d}"
            partners[pid] = _entry(
                make_index_entry, make_locator, pid, common_secs
            )
        canonicals = {a.id: a, **{p.id: p for p in partners.values()}}
        edges = build_see_also_edges(canonicals)
        assert len(edges["alpha"]) == 5
        # Should pick the alphabetically-first 5 (since all have equal co-count)
        assert edges["alpha"] == sorted(edges["alpha"])

    def test_alphabetical_tiebreak_within_top_n(
        self, make_index_entry, make_locator
    ):
        """Same co-count → alphabetical id wins."""
        common_secs = ["§2.04.1", "§2.04.2", "§2.04.3", "§2.04.4", "§2.04.5"]
        a = _entry(make_index_entry, make_locator, "alpha", common_secs)
        # Two candidates with equal co-count = 5; alphabetical wins
        partners = {
            "zeta": _entry(make_index_entry, make_locator, "zeta", common_secs),
            "beta": _entry(make_index_entry, make_locator, "beta", common_secs),
        }
        canonicals = {a.id: a, **partners}
        edges = build_see_also_edges(canonicals)
        # Output sorted alphabetically (per D-07)
        assert edges["alpha"] == ["beta", "zeta"]

    def test_higher_co_count_ranks_first(
        self, make_index_entry, make_locator
    ):
        """A pair with higher co-count outranks one with lower co-count."""
        # alpha shares 5 secs with high; alpha shares only 3 secs with low
        a = _entry(
            make_index_entry,
            make_locator,
            "alpha",
            ["§2.04.1", "§2.04.2", "§2.04.3", "§2.04.4", "§2.04.5"],
        )
        high = _entry(
            make_index_entry,
            make_locator,
            "high",
            ["§2.04.1", "§2.04.2", "§2.04.3", "§2.04.4", "§2.04.5"],
        )
        low = _entry(
            make_index_entry,
            make_locator,
            "low",
            ["§2.04.1", "§2.04.2", "§2.04.3"],
        )
        # Add 4 more low-count partners to push beyond out-degree=5
        extras = {}
        for i in range(4):
            pid = f"extra-{i:02d}"
            extras[pid] = _entry(
                make_index_entry,
                make_locator,
                pid,
                ["§2.04.1", "§2.04.2", "§2.04.3"],
            )
        canonicals = {
            "alpha": a,
            "high": high,
            "low": low,
            **{p.id: p for p in extras.values()},
        }
        edges = build_see_also_edges(canonicals)
        assert "high" in edges["alpha"]  # high co-count always retained
        assert len(edges["alpha"]) == 5

    def test_empty_input(self):
        assert build_see_also_edges({}) == {}


# ---------------------------------------------------------------------------
# find_cycle
# ---------------------------------------------------------------------------


class TestFindCycle:
    def test_dag_returns_no_cycle(self):
        edges = {"a": ["b"], "b": ["c"], "c": []}
        has_cycle, path = find_cycle(edges)
        assert has_cycle is False
        assert path is None

    def test_three_node_cycle(self):
        edges = {"a": ["b"], "b": ["c"], "c": ["a"]}
        has_cycle, path = find_cycle(edges)
        assert has_cycle is True
        # Path includes the back-edge target
        assert path[0] == path[-1]
        assert set(path) == {"a", "b", "c"}

    def test_self_loop(self):
        edges = {"a": ["a"]}
        has_cycle, path = find_cycle(edges)
        assert has_cycle is True

    def test_empty_graph(self):
        has_cycle, path = find_cycle({})
        assert has_cycle is False
        assert path is None

    def test_disconnected_components_one_cyclic(self):
        edges = {"a": ["b"], "b": [], "x": ["y"], "y": ["x"]}
        has_cycle, path = find_cycle(edges)
        assert has_cycle is True
        assert "x" in set(path) and "y" in set(path)


# ---------------------------------------------------------------------------
# find_dangling
# ---------------------------------------------------------------------------


class TestFindDangling:
    def test_no_dangling(self, make_index_entry, make_locator):
        e1 = _entry(make_index_entry, make_locator, "alpha", ["§2.04.1"])
        e2 = make_index_entry(
            id="beta",
            canonical="beta",
            sort_key="beta",
            locators=[make_locator()],
            see=["alpha"],
        )
        assert find_dangling([e1, e2]) == []

    def test_dangling_see(self, make_index_entry, make_locator):
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see=["missing-id"],
        )
        result = find_dangling([e1])
        assert result == [("alpha", "see", "missing-id")]

    def test_dangling_see_also(self, make_index_entry, make_locator):
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see_also=["nope"],
        )
        result = find_dangling([e1])
        assert result == [("alpha", "see_also", "nope")]

    def test_multiple_dangling(self, make_index_entry, make_locator):
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see=["missing-1", "missing-2"],
        )
        result = find_dangling([e1])
        assert ("alpha", "see", "missing-1") in result
        assert ("alpha", "see", "missing-2") in result


# ---------------------------------------------------------------------------
# validate_graph
# ---------------------------------------------------------------------------


class TestValidateGraph:
    def test_valid_graph_passes(self, make_index_entry, make_locator):
        e1 = _entry(make_index_entry, make_locator, "alpha", ["§2.04.1"])
        e2 = make_index_entry(
            id="beta",
            canonical="beta",
            sort_key="beta",
            locators=[make_locator()],
            see=["alpha"],
        )
        validate_graph([e1, e2])  # should not raise

    def test_see_cycle_raises(self, make_index_entry, make_locator):
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see=["beta"],
        )
        e2 = make_index_entry(
            id="beta",
            canonical="beta",
            sort_key="beta",
            locators=[make_locator()],
            see=["alpha"],
        )
        with pytest.raises(CycleDetectedError):
            validate_graph([e1, e2])

    def test_dangling_see_raises(self, make_index_entry, make_locator):
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see=["nonexistent"],
        )
        with pytest.raises(DanglingRefError):
            validate_graph([e1])

    def test_dangling_see_also_raises(self, make_index_entry, make_locator):
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see_also=["nonexistent"],
        )
        with pytest.raises(DanglingRefError):
            validate_graph([e1])

    def test_see_also_bidirectional_no_cycle_raise(
        self, make_index_entry, make_locator
    ):
        """see_also is undirected; A→B and B→A is the same edge."""
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see_also=["beta"],
        )
        e2 = make_index_entry(
            id="beta",
            canonical="beta",
            sort_key="beta",
            locators=[make_locator()],
            see_also=["alpha"],
        )
        validate_graph([e1, e2])  # MUST NOT raise

    def test_see_also_out_degree_over_5_raises(
        self, make_index_entry, make_locator
    ):
        """Defense-in-depth: out-degree > 5 is an AssertionError."""
        partners = [f"p-{i}" for i in range(6)]
        e1 = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see_also=partners,
        )
        partner_entries = [
            make_index_entry(
                id=p, canonical=p, sort_key=p, locators=[make_locator()]
            )
            for p in partners
        ]
        with pytest.raises(AssertionError):
            validate_graph([e1, *partner_entries])


# ---------------------------------------------------------------------------
# check_out_degree (helper)
# ---------------------------------------------------------------------------


class TestCheckOutDegree:
    def test_returns_offenders(self, make_index_entry, make_locator):
        partners = [f"p-{i}" for i in range(6)]
        e = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see_also=partners,
        )
        result = check_out_degree([e], max_see_also=5)
        assert result == [("alpha", 6)]

    def test_returns_empty_when_in_bounds(
        self, make_index_entry, make_locator
    ):
        e = make_index_entry(
            id="alpha",
            canonical="alpha",
            sort_key="alpha",
            locators=[make_locator()],
            see_also=["a", "b"],
        )
        assert check_out_degree([e], max_see_also=5) == []
