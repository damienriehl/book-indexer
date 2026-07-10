"""Unit tests for src/book_indexer/tables/ir.py.

Locks the Phase 3b IR shape:
- All 9 models are ``frozen=True`` + ``extra='forbid'``.
- ``Locator.section_ref`` matches Phase 2 ``Evidence.section_ref`` regex
  byte-for-byte.
- ``TableProvenance.frozen_timestamp`` is ``Literal[0]`` (Lock #5 defense).
- Round-trip via ``model_dump(mode="json")`` → ``model_validate`` is
  idempotent (locks JSON serialization shape).
- Envelopes enforce typed ``entries`` (TableOfCases rejects StatuteEntry).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from book_indexer.tables.ir import (
    CaseEntry,
    Locator,
    RuleEntry,
    StatuteEntry,
    SubsectionEntry,
    TableOfCases,
    TableOfRules,
    TableOfStatutes,
    TableProvenance,
)

# ---------------------------------------------------------------------------
# Builders — keep the round-trip tests readable
# ---------------------------------------------------------------------------


def _locator(section_ref: str = "§2.04", folio: str = "78", evidence_id: int = 1) -> Locator:
    return Locator(section_ref=section_ref, folio=folio, evidence_id=evidence_id)


def _provenance(**overrides) -> TableProvenance:
    base = dict(
        eyecite_version="2.7.6",
        reporters_db_version="3.2.64",
        courts_db_version="0.10.27",
        pdf_sha256="94503c16dcc3ce29d64be1591fec85eb94b83c4452622cfd9676bb604e553070",
        corpus_sha="abc123",
        jurisdictions_enabled=["us"],
        chapter_rule_systems={"1": "MRPC", "2": "FRCP"},
        cite_counts={"cases": 0, "statutes": 0, "rules": 0},
        regex_fallback_counts={"FRE": 0, "FRCP": 0, "FRAP": 0, "MRPC": 0},
        unresolved_short_cites=[],
        unverified_extractions=[],
    )
    base.update(overrides)
    return TableProvenance(**base)


# ---------------------------------------------------------------------------
# Locator
# ---------------------------------------------------------------------------


def test_locator_round_trip() -> None:
    loc = _locator()
    payload = loc.model_dump(mode="json")
    rebuilt = Locator.model_validate(payload)
    assert rebuilt == loc


def test_locator_section_ref_regex_rejects_bad_pattern() -> None:
    """Missing § sigil — must fail Phase 2 regex contract."""
    with pytest.raises(ValidationError):
        Locator(section_ref="2.04", folio="78", evidence_id=1)


def test_locator_evidence_id_ge_1() -> None:
    with pytest.raises(ValidationError):
        Locator(section_ref="§2.04", folio="78", evidence_id=0)


# ---------------------------------------------------------------------------
# Round-trip every entry type
# ---------------------------------------------------------------------------


def test_case_entry_round_trip() -> None:
    entry = CaseEntry(
        display_name="In re Smith",
        sort_key="Smith",
        canonical_citation="In re Smith, 410 U.S. 113 (1973)",
        reporter="U.S.",
        court="scotus",
        year=1973,
        locators=[_locator()],
    )
    rebuilt = CaseEntry.model_validate(entry.model_dump(mode="json"))
    assert rebuilt == entry


def test_statute_entry_round_trip() -> None:
    entry = StatuteEntry(
        display_name="42 U.S.C. § 1983",
        sort_key="42:1983",
        canonical_citation="42 U.S.C. § 1983",
        title="42",
        section="1983",
        publisher=None,
        locators=[_locator()],
    )
    rebuilt = StatuteEntry.model_validate(entry.model_dump(mode="json"))
    assert rebuilt == entry


def test_subsection_entry_round_trip() -> None:
    sub = SubsectionEntry(subsection_path="(b)(1)", locators=[_locator()])
    rebuilt = SubsectionEntry.model_validate(sub.model_dump(mode="json"))
    assert rebuilt == sub


def test_rule_entry_round_trip() -> None:
    entry = RuleEntry(
        parent_rule="FRE 404",
        rule_system="FRE",
        sort_key="FRE 404",
        parent_locators=[_locator()],
        subsections=[
            SubsectionEntry(subsection_path="(a)", locators=[_locator(folio="79")]),
            SubsectionEntry(subsection_path="(b)", locators=[_locator(folio="80")]),
        ],
    )
    rebuilt = RuleEntry.model_validate(entry.model_dump(mode="json"))
    assert rebuilt == entry


def test_table_provenance_round_trip() -> None:
    prov = _provenance()
    rebuilt = TableProvenance.model_validate(prov.model_dump(mode="json"))
    assert rebuilt == prov


def test_table_of_cases_round_trip() -> None:
    table = TableOfCases(
        schema_version="1",
        entries=[
            CaseEntry(
                display_name="Smith v. Jones",
                sort_key="Smith v. Jones",
                canonical_citation="Smith v. Jones, 1 F.3d 1 (1st Cir. 1993)",
                reporter="F.3d",
                court="ca1",
                year=1993,
                locators=[_locator()],
            )
        ],
        provenance=_provenance(),
    )
    rebuilt = TableOfCases.model_validate(table.model_dump(mode="json"))
    assert rebuilt == table


# ---------------------------------------------------------------------------
# extra='forbid' — parametrize over every model
# ---------------------------------------------------------------------------


_VALID_LOCATOR_PAYLOAD = {"section_ref": "§2.04", "folio": "78", "evidence_id": 1}
_VALID_CASE_ENTRY_PAYLOAD = {
    "display_name": "In re Smith",
    "sort_key": "Smith",
    "canonical_citation": "In re Smith, 410 U.S. 113 (1973)",
    "reporter": "U.S.",
    "court": "scotus",
    "year": 1973,
    "locators": [_VALID_LOCATOR_PAYLOAD],
}
_VALID_STATUTE_ENTRY_PAYLOAD = {
    "display_name": "42 U.S.C. § 1983",
    "sort_key": "42:1983",
    "canonical_citation": "42 U.S.C. § 1983",
    "title": "42",
    "section": "1983",
    "publisher": None,
    "locators": [_VALID_LOCATOR_PAYLOAD],
}
_VALID_SUBSECTION_PAYLOAD = {
    "subsection_path": "(a)",
    "locators": [_VALID_LOCATOR_PAYLOAD],
}
_VALID_RULE_PAYLOAD = {
    "parent_rule": "FRE 404",
    "rule_system": "FRE",
    "sort_key": "FRE 404",
    "parent_locators": [_VALID_LOCATOR_PAYLOAD],
    "subsections": [_VALID_SUBSECTION_PAYLOAD],
}
_VALID_PROVENANCE_PAYLOAD = {
    "eyecite_version": "2.7.6",
    "reporters_db_version": "3.2.64",
    "courts_db_version": "0.10.27",
    "pdf_sha256": "94503c16dcc3ce29d64be1591fec85eb94b83c4452622cfd9676bb604e553070",
    "corpus_sha": "abc123",
    "jurisdictions_enabled": ["us"],
    "chapter_rule_systems": {"1": "MRPC"},
    "cite_counts": {},
    "regex_fallback_counts": {},
    "unresolved_short_cites": [],
    "unverified_extractions": [],
    "frozen_timestamp": 0,
}


@pytest.mark.parametrize(
    "model_cls,base_payload",
    [
        (Locator, _VALID_LOCATOR_PAYLOAD),
        (CaseEntry, _VALID_CASE_ENTRY_PAYLOAD),
        (StatuteEntry, _VALID_STATUTE_ENTRY_PAYLOAD),
        (SubsectionEntry, _VALID_SUBSECTION_PAYLOAD),
        (RuleEntry, _VALID_RULE_PAYLOAD),
        (TableProvenance, _VALID_PROVENANCE_PAYLOAD),
        (
            TableOfCases,
            {
                "schema_version": "1",
                "entries": [_VALID_CASE_ENTRY_PAYLOAD],
                "provenance": _VALID_PROVENANCE_PAYLOAD,
            },
        ),
        (
            TableOfStatutes,
            {
                "schema_version": "1",
                "entries": [_VALID_STATUTE_ENTRY_PAYLOAD],
                "provenance": _VALID_PROVENANCE_PAYLOAD,
            },
        ),
        (
            TableOfRules,
            {
                "schema_version": "1",
                "entries": [_VALID_RULE_PAYLOAD],
                "provenance": _VALID_PROVENANCE_PAYLOAD,
            },
        ),
    ],
)
def test_extra_forbid_on_every_model(model_cls, base_payload: dict) -> None:
    """Every IR model rejects unknown fields with extra_forbidden."""
    payload = {**base_payload, "page": 78}  # ← page is the canonical attack
    with pytest.raises(ValidationError) as exc_info:
        model_cls.model_validate(payload)
    errors = exc_info.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errors), (
        f"{model_cls.__name__} did not raise extra_forbidden; got {errors}"
    )


# ---------------------------------------------------------------------------
# Lock #5 — frozen_timestamp is Literal[0]
# ---------------------------------------------------------------------------


def test_table_provenance_frozen_timestamp_must_be_zero() -> None:
    payload = {**_VALID_PROVENANCE_PAYLOAD, "frozen_timestamp": 1}
    with pytest.raises(ValidationError):
        TableProvenance.model_validate(payload)


# ---------------------------------------------------------------------------
# RuleEntry rule_system — MRPC + Wave 0 systems all accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("system", ["FRE", "FRCP", "FRAP", "FedR", "Rule", "MRPC"])
def test_rule_system_literal_includes_all_systems(system: str) -> None:
    rule = RuleEntry(
        parent_rule="FRE 404",
        rule_system=system,  # type: ignore[arg-type]
        sort_key="FRE 404",
        parent_locators=[_locator()],
        subsections=[],
    )
    assert rule.rule_system == system


def test_rule_system_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        RuleEntry(
            parent_rule="FRE 404",
            rule_system="UCC",  # type: ignore[arg-type]
            sort_key="FRE 404",
            parent_locators=[_locator()],
            subsections=[],
        )


# ---------------------------------------------------------------------------
# Envelopes — typed entries enforced
# ---------------------------------------------------------------------------


def test_envelope_holds_correct_entry_type() -> None:
    """TableOfCases rejects a StatuteEntry payload in entries."""
    with pytest.raises(ValidationError):
        TableOfCases.model_validate(
            {
                "schema_version": "1",
                "entries": [_VALID_STATUTE_ENTRY_PAYLOAD],
                "provenance": _VALID_PROVENANCE_PAYLOAD,
            }
        )


# ---------------------------------------------------------------------------
# Frozen — instances are immutable
# ---------------------------------------------------------------------------


def test_locator_is_frozen() -> None:
    loc = _locator()
    with pytest.raises(ValidationError):
        loc.folio = "999"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Re-export contract — package surface exposes every IR class
# ---------------------------------------------------------------------------


def test_package_re_exports_ir() -> None:
    """from book_indexer.tables import * resolves the 9 IR types."""
    import book_indexer.tables as pkg

    for name in (
        "Locator",
        "CaseEntry",
        "StatuteEntry",
        "SubsectionEntry",
        "RuleEntry",
        "TableProvenance",
        "TableOfCases",
        "TableOfStatutes",
        "TableOfRules",
    ):
        assert hasattr(pkg, name), f"book_indexer.tables missing {name}"
