"""Unit tests for src/book_indexer/assembly/ir.py.

requirements_addressed: ASM-08 (canonical IndexTree.json IR — frozen
Pydantic schema; dual-locator Locator(section_ref, folio, evidence_id)
shape locked at this layer).

Locks the Phase 4 IR shape per RESEARCH §H-10 + D-07:
- IndexTree, IndexEntry, SubEntry, IndexTreeProvenance are
  ``frozen=True`` + ``extra='forbid'``.
- ``Locator`` is REUSED from ``book_indexer.tables.ir`` (single source
  of truth) — assembly never redefines it.
- ``IndexEntry.id`` regex enforces lowercase slugs with optional numeric
  collision suffix.
- ``IndexTreeProvenance.frozen_timestamp`` is ``Literal[0]`` (Lock #5).
- Round-trip via ``model_dump(mode="json")`` → ``model_validate`` is
  idempotent (locks JSON serialization shape).
- ``IndexTree.entries`` is a typed list (rejects bare dict drift).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from book_indexer.assembly import (
    IndexEntry,
    IndexTree,
    IndexTreeProvenance,
    Locator,
    SubEntry,
)
from book_indexer.assembly import ir as assembly_ir
from book_indexer.tables import ir as tables_ir


# ---------------------------------------------------------------------------
# Locator is the SAME class — single source of truth
# ---------------------------------------------------------------------------


def test_locator_is_single_source_of_truth() -> None:
    """``Locator`` exposed by book_indexer.assembly is the SAME class
    as the one in book_indexer.tables.ir. Redefinition is a Lock #2
    ship-blocker (schema would drift between Phase 3b and Phase 4)."""
    assert Locator is tables_ir.Locator


def test_assembly_ir_does_not_redefine_locator() -> None:
    """``assembly/ir.py`` MUST NOT contain a top-level ``class Locator``
    declaration — only an import. If this fails, find and delete the
    redefinition."""
    import inspect

    source = inspect.getsource(assembly_ir)
    # The only Locator reference allowed in assembly/ir.py is the import line.
    # Any literal ``class Locator`` declaration is prohibited.
    assert "class Locator" not in source, (
        "assembly/ir.py contains a class Locator declaration — "
        "Lock #2 ship-blocker; import from book_indexer.tables.ir instead."
    )


# ---------------------------------------------------------------------------
# Round-trip every entry type
# ---------------------------------------------------------------------------


def test_sub_entry_round_trip(make_sub_entry) -> None:
    sub = make_sub_entry()
    rebuilt = SubEntry.model_validate(sub.model_dump(mode="json"))
    assert rebuilt == sub


def test_index_entry_round_trip(make_index_entry) -> None:
    entry = make_index_entry()
    rebuilt = IndexEntry.model_validate(entry.model_dump(mode="json"))
    assert rebuilt == entry


def test_index_entry_round_trip_with_sub_entries(make_index_entry, make_sub_entry, make_locator) -> None:
    entry = make_index_entry(
        sub_entries=[
            make_sub_entry(text="qualifications", sort_key="qualifications"),
            make_sub_entry(
                text="batson challenges",
                sort_key="batson challenges",
                locators=[make_locator(folio="79")],
            ),
        ],
        see=["jury-selection"],
        see_also=["challenges-for-cause"],
        variants=["voir-dire-examination"],
    )
    rebuilt = IndexEntry.model_validate(entry.model_dump(mode="json"))
    assert rebuilt == entry


def test_provenance_round_trip(make_provenance) -> None:
    prov = make_provenance()
    rebuilt = IndexTreeProvenance.model_validate(prov.model_dump(mode="json"))
    assert rebuilt == prov


def test_index_tree_round_trip(make_index_tree) -> None:
    tree = make_index_tree()
    rebuilt = IndexTree.model_validate(tree.model_dump(mode="json"))
    assert rebuilt == tree


# ---------------------------------------------------------------------------
# extra='forbid' — parametrize over every Phase 4 model
# ---------------------------------------------------------------------------


_VALID_LOCATOR_PAYLOAD = {"section_ref": "§2.04", "folio": "78", "evidence_id": 1}
_VALID_SUB_ENTRY_PAYLOAD = {
    "text": "examination of jurors",
    "sort_key": "examination of jurors",
    "locators": [_VALID_LOCATOR_PAYLOAD],
}
_VALID_INDEX_ENTRY_PAYLOAD = {
    "id": "voir-dire",
    "canonical": "voir dire",
    "sort_key": "voir dire",
    "derived_from_table": None,
    "locators": [_VALID_LOCATOR_PAYLOAD],
    "sub_entries": [],
    "see": [],
    "see_also": [],
    "variants": [],
}
_VALID_PROVENANCE_PAYLOAD = {
    "spacy_version": "3.8.14",
    "spacy_model_sha": "abc123",
    "eyecite_version": "2.7.6",
    "reporters_db_version": "3.2.64",
    "courts_db_version": "0.10.27",
    "pdf_sha256": "94503c16dcc3ce29d64be1591fec85eb94b83c4452622cfd9676bb604e553070",
    "corpus_sha": "cafebabe",
    "concepts_sha": {},
    "tables_sha": {},
    "pre_dedup_count": 0,
    "post_dedup_count": 0,
    "post_deconflict_count": 0,
    "post_zero_evidence_count": 0,
    "oversize_parent_count": 0,
    "sub_entry_total_count": 0,
    "oob_status": "none",
    "max_sub_entries_per_parent": 0,
    "parents_with_no_locators": 0,
    "dropped_table_citations": [],
    "zero_evidence_drops": [],
    "slug_collision_count": 0,
    "iteration_depth": 1,
    "frozen_timestamp": 0,
}
_VALID_INDEX_TREE_PAYLOAD = {
    "schema_version": "1",
    "provenance": _VALID_PROVENANCE_PAYLOAD,
    "entries": [_VALID_INDEX_ENTRY_PAYLOAD],
}


@pytest.mark.parametrize(
    "model_cls,base_payload",
    [
        (SubEntry, _VALID_SUB_ENTRY_PAYLOAD),
        (IndexEntry, _VALID_INDEX_ENTRY_PAYLOAD),
        (IndexTreeProvenance, _VALID_PROVENANCE_PAYLOAD),
        (IndexTree, _VALID_INDEX_TREE_PAYLOAD),
    ],
)
def test_extra_forbid_on_every_model(model_cls, base_payload: dict) -> None:
    """Every Phase 4 IR model rejects unknown fields. ``page`` is the
    canonical attack — if ANY field with that name slips into the IR,
    Lock #4 (printed folio is the public citation) is at risk."""
    payload = {**base_payload, "page": 78}
    with pytest.raises(ValidationError) as exc_info:
        model_cls.model_validate(payload)
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors), (
        f"{model_cls.__name__} did not raise extra_forbidden; got {errors}"
    )


# ---------------------------------------------------------------------------
# IndexEntry.id regex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valid_id",
    [
        "voir-dire",
        "voir-dire-2",
        "frcp-12",
        "rule-of-completeness",
        "a",  # single lowercase letter
        "x1",  # alphanumeric ok
        "123",  # leading digit ok by [a-z0-9]
        "smith-v-jones-2",
    ],
)
def test_index_entry_id_regex_accepts_valid(valid_id: str, make_index_entry) -> None:
    entry = make_index_entry(id=valid_id)
    assert entry.id == valid_id


@pytest.mark.parametrize(
    "invalid_id",
    [
        "Voir-dire",  # uppercase
        "VOIR_DIRE",  # uppercase + underscore
        "-voir",  # leading hyphen
        "",  # empty
        "voir dire",  # space
        "voir_dire",  # underscore
        "voir.dire",  # dot
    ],
)
def test_index_entry_id_regex_rejects_invalid(invalid_id: str) -> None:
    with pytest.raises(ValidationError):
        IndexEntry.model_validate({**_VALID_INDEX_ENTRY_PAYLOAD, "id": invalid_id})


# ---------------------------------------------------------------------------
# Lock #5 — frozen_timestamp is Literal[0]
# ---------------------------------------------------------------------------


def test_provenance_frozen_timestamp_must_be_zero() -> None:
    payload = {**_VALID_PROVENANCE_PAYLOAD, "frozen_timestamp": 1}
    with pytest.raises(ValidationError):
        IndexTreeProvenance.model_validate(payload)


def test_provenance_frozen_timestamp_default_zero(make_provenance) -> None:
    """frozen_timestamp defaults to 0 (no need to pass it explicitly)."""
    prov = make_provenance()
    assert prov.frozen_timestamp == 0


# ---------------------------------------------------------------------------
# derived_from_table enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["cases", "statutes", "rules", None])
def test_derived_from_table_accepts_allowed(value, make_index_entry) -> None:
    entry = make_index_entry(derived_from_table=value)
    assert entry.derived_from_table == value


def test_derived_from_table_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        IndexEntry.model_validate(
            {**_VALID_INDEX_ENTRY_PAYLOAD, "derived_from_table": "opinions"}
        )


# ---------------------------------------------------------------------------
# Empty locators are structurally permitted on IndexEntry (post-build
# invariant test in Plan 04-03 enforces non-empty in the FINAL tree)
# ---------------------------------------------------------------------------


def test_index_entry_accepts_empty_locators(make_index_entry) -> None:
    entry = make_index_entry(locators=[])
    assert entry.locators == []


# ---------------------------------------------------------------------------
# Frozen — instances are immutable
# ---------------------------------------------------------------------------


def test_sub_entry_is_frozen(make_sub_entry) -> None:
    sub = make_sub_entry()
    with pytest.raises(ValidationError):
        sub.text = "mutated"  # type: ignore[misc]


def test_index_entry_is_frozen(make_index_entry) -> None:
    entry = make_index_entry()
    with pytest.raises(ValidationError):
        entry.canonical = "mutated"  # type: ignore[misc]


def test_index_tree_is_frozen(make_index_tree) -> None:
    tree = make_index_tree()
    with pytest.raises(ValidationError):
        tree.schema_version = "999"  # type: ignore[misc]


def test_provenance_is_frozen(make_provenance) -> None:
    prov = make_provenance()
    with pytest.raises(ValidationError):
        prov.iteration_depth = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# IndexTree.entries is a list (not a tuple); typed; rejects wrong shape
# ---------------------------------------------------------------------------


def test_index_tree_entries_is_list(make_index_tree) -> None:
    tree = make_index_tree()
    assert isinstance(tree.entries, list)


def test_index_tree_rejects_non_index_entry_in_entries() -> None:
    """A bare dict missing required IndexEntry fields fails at envelope
    validation."""
    with pytest.raises(ValidationError):
        IndexTree.model_validate(
            {
                "schema_version": "1",
                "provenance": _VALID_PROVENANCE_PAYLOAD,
                "entries": [{"id": "voir-dire"}],  # missing canonical, sort_key, etc.
            }
        )


# ---------------------------------------------------------------------------
# oob_status enum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["none", "under", "over"])
def test_oob_status_accepts_allowed(value, make_provenance) -> None:
    prov = make_provenance(oob_status=value)
    assert prov.oob_status == value


def test_oob_status_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        IndexTreeProvenance.model_validate(
            {**_VALID_PROVENANCE_PAYLOAD, "oob_status": "WAY_OVER"}
        )


# ---------------------------------------------------------------------------
# Re-export contract — package surface exposes every Phase 4 IR class
# ---------------------------------------------------------------------------


def test_package_re_exports_ir() -> None:
    """``from book_indexer.assembly import *`` resolves the Phase 4 IR
    types plus the re-exported Locator and the four typed exceptions."""
    import book_indexer.assembly as pkg

    for name in (
        "Locator",
        "SubEntry",
        "IndexEntry",
        "IndexTreeProvenance",
        "IndexTree",
        "AssemblyError",
        "CycleDetectedError",
        "DanglingRefError",
        "EmptyConceptsError",
        "OversizeAfterIterationError",
    ):
        assert hasattr(pkg, name), f"book_indexer.assembly missing {name}"
