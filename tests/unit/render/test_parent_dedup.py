"""v1.2.2 unit tests for render/parent_dedup.py.

Covers the user-locked decisions:

  * Same-first-word dedup: standalone is dropped, child stays under
    the synthetic parent.
  * Different-first-word preservation: standalone STAYS as the reader's
    alphabetical anchor (e.g. ``manual for complex litigation`` under M
    even though it's a child of the ``complex`` synth).
  * Variant transfer: the standalone's ``*(also: …)*`` variants are
    re-attached to the surviving child via the side-channel map.
  * Locator-mismatch safety: standalone is KEPT when its locators
    differ from the child's (defensive against IR coherence loss).
  * Idempotence: running the pass twice is the same as once.

Lock #1 preserved: this module never imports ``verify``.
Lock #5: pure-functional + sorted iteration + frozen dataclass.

requirements_addressed: v1.2.2 parent dedup.
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from book_indexer.assembly.ir import IndexEntry
from book_indexer.render.cross_refs import CrossRefEntry
from book_indexer.render.ir import SyntheticEntry
from book_indexer.render.parent_dedup import (
    ParentDedupResult,
    dedupe_parent_aliased_standalones,
)
from book_indexer.render.plural_consolidation import ConsolidatedEntry
from book_indexer.tables.ir import Locator


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _loc(section_ref: str = "§1.01", folio: str = "1", evidence_id: int = 1) -> Locator:
    return Locator(section_ref=section_ref, folio=folio, evidence_id=evidence_id)


def _entry(
    canonical: str,
    *,
    sort_key: str | None = None,
    locators: list[Locator] | None = None,
    variants: list[str] | None = None,
) -> IndexEntry:
    sk = sort_key if sort_key is not None else canonical.lower()
    slug = canonical.lower().replace(" ", "-")
    return IndexEntry(
        id=slug,
        canonical=canonical,
        sort_key=sk,
        locators=locators if locators is not None else [_loc()],
        variants=variants or [],
        sub_entries=[],
    )


def _synth(stem: str, sibling_canonicals: list[str], locators: list[Locator] | None = None):
    return SyntheticEntry(
        stem=stem,
        sibling_canonicals=tuple(sorted(sibling_canonicals)),
        locators=tuple(locators if locators is not None else [_loc()]),
    )


def _entry_item(entry: IndexEntry):
    return (entry.sort_key, "entry", entry)


def _synth_item(synth: SyntheticEntry):
    return (synth.stem.lower(), "synth", synth)


def _xref_item(xref: CrossRefEntry):
    return (xref.sort_key, "xref", xref)


def _consolidated_item(c: ConsolidatedEntry, sort_key: str):
    return (sort_key, "consolidated", c)


# ---------------------------------------------------------------------------
# 1. Same-first-word dedup (the headline behavior)
# ---------------------------------------------------------------------------


def test_same_first_word_drops_standalone() -> None:
    """``complex case`` is a child of ``complex`` AND a standalone.
    Drop the standalone; keep the child under the synth parent.
    """
    locs = [_loc(section_ref="§1.07.13", folio="13")]
    child = _entry("complex case", locators=locs)
    synth = _synth("complex", ["complex case"], locators=locs)
    by_canonical = {child.canonical: child}

    stream = [_synth_item(synth), _entry_item(child)]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )

    # Standalone removed; only synth remains.
    assert len(new_stream) == 1
    assert new_stream[0][1] == "synth"
    assert new_stream[0][2].stem == "complex"
    # No variants to transfer.
    assert transferred == {}
    # Decision recorded.
    assert len(decisions) == 1
    assert decisions[0].standalone_dropped is True
    assert decisions[0].reason == "prefix_match"
    assert decisions[0].parent_stem == "complex"
    assert decisions[0].child_canonical == "complex case"


def test_different_first_word_keeps_standalone() -> None:
    """``manual for complex litigation`` is a child of ``complex`` but
    starts with ``manual`` — keep the standalone (alphabetical anchor
    under M).
    """
    locs = [_loc(section_ref="§2.08.6", folio="25")]
    child = _entry("manual for complex litigation", locators=locs)
    synth = _synth(
        "complex", ["manual for complex litigation"], locators=locs
    )
    by_canonical = {child.canonical: child}

    stream = [_synth_item(synth), _entry_item(child)]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )

    # Both items survive.
    assert len(new_stream) == 2
    kinds = sorted(item[1] for item in new_stream)
    assert kinds == ["entry", "synth"]
    # No transfer.
    assert transferred == {}
    # Decision recorded as different_first_word.
    assert len(decisions) == 1
    assert decisions[0].standalone_dropped is False
    assert decisions[0].reason == "different_first_word"


def test_multiple_children_mixed_first_words() -> None:
    """Realistic ``complex`` parent with 4 children — 3 same-first-word
    (drop standalones) + 1 different-first-word (keep standalone).
    """
    loc_a = _loc(section_ref="§1.07.13", folio="13")
    loc_b = _loc(section_ref="§2.04.1", folio="18")
    loc_c = _loc(section_ref="§2.08.6", folio="25")
    loc_d = _loc(section_ref="§2.08.11", folio="27")

    cc = _entry("complex case", locators=[loc_a, loc_b])
    ccc = _entry("complex case complex", locators=[loc_d])
    cl = _entry("complex litigation", locators=[loc_a, loc_c])
    mfcl = _entry("manual for complex litigation", locators=[loc_c])
    synth = _synth(
        "complex",
        ["complex case", "complex case complex", "complex litigation",
         "manual for complex litigation"],
        locators=[loc_a, loc_b, loc_c, loc_d],
    )
    by_canonical = {
        cc.canonical: cc,
        ccc.canonical: ccc,
        cl.canonical: cl,
        mfcl.canonical: mfcl,
    }

    stream = [
        _synth_item(synth),
        _entry_item(cc),
        _entry_item(ccc),
        _entry_item(cl),
        _entry_item(mfcl),
    ]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )

    # Synth + 1 surviving standalone (manual for complex litigation).
    assert len(new_stream) == 2
    surviving_canonicals = {
        item[2].canonical
        for item in new_stream
        if item[1] == "entry"
    }
    assert surviving_canonicals == {"manual for complex litigation"}
    # 4 decisions — 3 prefix_match (dropped) + 1 different_first_word.
    assert len(decisions) == 4
    dropped = [d for d in decisions if d.standalone_dropped]
    assert {d.child_canonical for d in dropped} == {
        "complex case", "complex case complex", "complex litigation"
    }


# ---------------------------------------------------------------------------
# 2. Variant transfer
# ---------------------------------------------------------------------------


def test_variant_transfer_on_dedup() -> None:
    """Standalone's ``*(also: ...)*`` variants are stashed for re-emission
    on the surviving child line.
    """
    locs = [_loc(section_ref="§2.08.11", folio="27")]
    child = _entry(
        "complex case complex",
        locators=locs,
        variants=["Complex Cases Complexes", "Complex Cases Complex"],
    )
    synth = _synth("complex", ["complex case complex"], locators=locs)
    by_canonical = {child.canonical: child}

    stream = [_synth_item(synth), _entry_item(child)]
    _new_stream, transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )

    # Variants transferred to the side-channel.
    assert "complex case complex" in transferred
    assert transferred["complex case complex"] == (
        "Complex Cases Complexes",
        "Complex Cases Complex",
    )
    # Decision is still a clean prefix_match drop.
    assert decisions[0].standalone_dropped is True


def test_variant_transfer_skipped_when_no_variants() -> None:
    """Standalone with no variants → empty transferred map."""
    locs = [_loc()]
    child = _entry("case management", locators=locs)
    synth = _synth("case", ["case management"], locators=locs)
    by_canonical = {child.canonical: child}

    stream = [_synth_item(synth), _entry_item(child)]
    _new, transferred, _decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )
    assert transferred == {}


# ---------------------------------------------------------------------------
# 3. Locator-mismatch safety
# ---------------------------------------------------------------------------


def test_locator_mismatch_keeps_both() -> None:
    """When standalone locators differ from child locators (set
    inequality), KEEP BOTH and record different_locators_kept_both.

    This requires entries_by_canonical to map the child to a DIFFERENT
    IndexEntry instance than what's in the stream — simulates IR
    coherence loss.
    """
    standalone_locs = [_loc(section_ref="§1.07.13", folio="13")]
    child_locs = [_loc(section_ref="§9.99.9", folio="999")]  # divergent
    standalone_entry = _entry("complex case", locators=standalone_locs)
    child_entry = _entry("complex case", locators=child_locs)
    synth = _synth("complex", ["complex case"], locators=child_locs)
    # entries_by_canonical points at the divergent (child) instance.
    by_canonical = {child_entry.canonical: child_entry}

    stream = [_synth_item(synth), _entry_item(standalone_entry)]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )

    # Standalone PRESERVED.
    assert len(new_stream) == 2
    assert any(item[1] == "entry" for item in new_stream)
    # No variant transfer.
    assert transferred == {}
    # Decision tagged as locator-mismatch keep-both.
    assert len(decisions) == 1
    assert decisions[0].standalone_dropped is False
    assert decisions[0].reason == "different_locators_kept_both"


# ---------------------------------------------------------------------------
# 4. Anti-regression — items that should never be touched
# ---------------------------------------------------------------------------


def test_synth_with_no_standalones_untouched() -> None:
    """Synth whose children have NO matching top-level standalones —
    new_stream is unchanged.
    """
    synth = _synth("complex", ["complex case", "complex litigation"])
    # No matching entries in the stream.
    other_entry = _entry("zebra")
    stream = [_synth_item(synth), _entry_item(other_entry)]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(stream)

    assert len(new_stream) == 2
    assert transferred == {}
    # Both children → prefix_match decisions with no drop.
    assert all(d.reason == "prefix_match" for d in decisions)
    assert all(d.standalone_dropped is False for d in decisions)


def test_standalones_without_synth_parent_untouched() -> None:
    """Top-level entries with no corresponding synth parent — unaffected."""
    entries = [_entry("alpha"), _entry("beta"), _entry("gamma")]
    stream = [_entry_item(e) for e in entries]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(stream)

    assert new_stream == stream
    assert transferred == {}
    assert decisions == []


def test_xref_items_passthrough() -> None:
    """Cross-refs are passed through verbatim."""
    xref = CrossRefEntry(head="agency", primary_canonical="administrative agency", sort_key="agency")
    stream = [_xref_item(xref)]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(stream)
    assert new_stream == stream
    assert transferred == {}
    assert decisions == []


def test_consolidated_items_treated_as_standalones() -> None:
    """A ConsolidatedEntry whose primary_canonical matches a synth child
    IS treated as a standalone (the consolidated form is the user-visible
    top-level line) — and gets dropped on prefix-match.
    """
    locs = [_loc()]
    consolidated = ConsolidatedEntry(
        display_canonical="case(s)",
        primary_canonical="case",
        locators=tuple(locs),
        see_target=None,
        variants=(),
        sub_entries=(),
        source_kind="primary",
    )
    # Parent stem 'case', child 'case' (the consolidated singular).
    case_child_entry = _entry("case", locators=locs)
    synth = _synth("case", ["case"], locators=locs)
    by_canonical = {"case": case_child_entry}

    stream = [_synth_item(synth), _consolidated_item(consolidated, "case")]
    new_stream, _transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )
    # The consolidated standalone IS dropped.
    assert len(new_stream) == 1
    assert new_stream[0][1] == "synth"
    assert decisions[0].standalone_dropped is True


# ---------------------------------------------------------------------------
# 5. Idempotence
# ---------------------------------------------------------------------------


def test_idempotent_two_runs_equal_one() -> None:
    """Running the pass twice yields the same stream as running it once."""
    locs = [_loc(section_ref="§1.07.13", folio="13")]
    child = _entry("complex case", locators=locs)
    other = _entry("manual for complex litigation", locators=locs)
    synth = _synth(
        "complex", ["complex case", "manual for complex litigation"],
        locators=locs,
    )
    by_canonical = {child.canonical: child, other.canonical: other}

    stream = [_synth_item(synth), _entry_item(child), _entry_item(other)]
    once, _t1, _d1 = dedupe_parent_aliased_standalones(stream, by_canonical)
    twice, _t2, _d2 = dedupe_parent_aliased_standalones(once, by_canonical)
    assert once == twice


# ---------------------------------------------------------------------------
# 6. Hypothesis property: idempotence on randomized child sets
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    children=st.lists(
        st.sampled_from([
            "complex case", "complex litigation", "complex matter",
            "complex case complex", "manual for complex litigation",
            "uncomplex setup", "compliance case",
        ]),
        min_size=1,
        max_size=7,
        unique=True,
    )
)
def test_idempotent_property(children: list[str]) -> None:
    locs = [_loc()]
    entries = {c: _entry(c, locators=locs) for c in children}
    synth = _synth("complex", children, locators=locs)
    stream = [_synth_item(synth)] + [_entry_item(e) for e in entries.values()]
    once, _t1, _d1 = dedupe_parent_aliased_standalones(stream, entries)
    twice, _t2, _d2 = dedupe_parent_aliased_standalones(once, entries)
    assert once == twice


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------


def test_empty_stream() -> None:
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones([])
    assert new_stream == []
    assert transferred == {}
    assert decisions == []


def test_synth_only_no_entries() -> None:
    synth = _synth("alpha", ["alpha thing", "alpha matter", "beta thing"])
    stream = [_synth_item(synth)]
    new_stream, transferred, decisions = dedupe_parent_aliased_standalones(stream)
    assert new_stream == stream
    assert transferred == {}
    # 3 children → 2 same-first-word (prefix_match no-op since no
    # standalones exist) + 1 different-first-word.
    assert len(decisions) == 3


def test_decisions_sorted_by_synth_stem() -> None:
    """Decisions list iterates synth parents in stem-sorted order
    (Lock #5 byte-determinism).
    """
    locs = [_loc()]
    s_zeta = _synth("zeta", ["zeta one"], locators=locs)
    s_alpha = _synth("alpha", ["alpha one"], locators=locs)
    e_zeta = _entry("zeta one", locators=locs)
    e_alpha = _entry("alpha one", locators=locs)
    by_canonical = {e_zeta.canonical: e_zeta, e_alpha.canonical: e_alpha}

    # Stream order: zeta first, alpha second — but decisions should be
    # alpha-first by sorted stem.
    stream = [
        _synth_item(s_zeta),
        _synth_item(s_alpha),
        _entry_item(e_zeta),
        _entry_item(e_alpha),
    ]
    _new, _transferred, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )
    assert [d.parent_stem for d in decisions] == ["alpha", "zeta"]


def test_substring_not_first_word_not_deduped() -> None:
    """``compliance case`` shares substring ``ompli`` with ``complex`` —
    but its first word is ``compliance``, NOT ``complex``. Standalone
    must be PRESERVED.
    """
    locs = [_loc()]
    standalone = _entry("compliance case", locators=locs)
    synth = _synth("complex", ["compliance case"], locators=locs)
    by_canonical = {standalone.canonical: standalone}

    stream = [_synth_item(synth), _entry_item(standalone)]
    new_stream, _t, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )
    assert len(new_stream) == 2
    assert decisions[0].reason == "different_first_word"


def test_case_insensitive_first_word_match() -> None:
    """``Complex Case`` (TitleCase canonical) matches stem ``complex``
    case-insensitively.
    """
    locs = [_loc()]
    standalone = _entry("Complex Case", locators=locs)
    synth = _synth("complex", ["Complex Case"], locators=locs)
    by_canonical = {standalone.canonical: standalone}

    stream = [_synth_item(synth), _entry_item(standalone)]
    new_stream, _t, decisions = dedupe_parent_aliased_standalones(
        stream, by_canonical
    )
    assert len(new_stream) == 1
    assert new_stream[0][1] == "synth"
    assert decisions[0].standalone_dropped is True
