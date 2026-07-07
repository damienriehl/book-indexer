"""Wave 0 sign-off ship-blocker for fixtures/acronym_overrides.yaml.

Per D-01, the acronym ↔ spelled-out force-merge map is hand-curated by
the author. Phase 4's dedup.py reads this YAML at canonicalization time
to collapse "FRE" + "Federal Rules of Evidence" into a single canonical
bucket. These tests fail CI if Wave 0 was bypassed (i.e. ``curated_by``
is still ``PENDING_AUTHOR``) or if the fixture is malformed.

requirements_addressed: ASM-01 (canonical-form selection — author confirms
the acronym ↔ spelled-out merge target).

Mirrors the test style of tests/unit/tables/test_chapter_rule_systems_loadable.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "acronym_overrides.yaml"


@pytest.fixture(scope="module")
def acronym_overrides() -> dict:
    """Load the YAML once per module."""
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_exists() -> None:
    assert FIXTURE.exists(), f"{FIXTURE} missing — Wave 0 incomplete"


def test_yaml_loads(acronym_overrides: dict) -> None:
    assert isinstance(acronym_overrides, dict), "top-level must be a mapping"
    assert "metadata" in acronym_overrides
    assert "acronyms" in acronym_overrides
    assert isinstance(acronym_overrides["acronyms"], list)


def test_author_signed_off(acronym_overrides: dict) -> None:
    """Wave 0 sign-off ship-blocker.

    If this fails, Wave 0 (Plan 04-00) was bypassed. Re-run
    ``/gsd-execute-phase 04 --plan 00`` and complete the
    author-confirmation checkpoint before proceeding to Wave 1.
    """
    curated_by = acronym_overrides["metadata"].get("curated_by", "")
    assert curated_by != "PENDING_AUTHOR", (
        "Wave 0 author sign-off NOT received — fixtures/acronym_overrides.yaml "
        "still has metadata.curated_by == 'PENDING_AUTHOR'. Run Plan 04-00 first."
    )
    assert curated_by, "curated_by must be a non-empty string"


def test_minimum_acronyms_present(acronym_overrides: dict) -> None:
    """Sanity: the bare minimum legal acronyms are mapped.

    These five are the irreducible core that Phase 4 dedup.py needs to
    canonicalize correctly. Striking any of them during author sign-off
    is forbidden — drift here breaks the FRE/FRCP/FRAP/MRPC/ALJ index
    entries.
    """
    acronyms = {row["acronym"]: row["spelled_out"] for row in acronym_overrides["acronyms"]}
    required = {"FRE", "FRCP", "FRAP", "MRPC", "ALJ"}
    missing = required - set(acronyms)
    assert not missing, f"required acronyms missing from fixture: {missing}"


@pytest.mark.parametrize("required_field", ["acronym", "spelled_out"])
def test_every_row_has_required_fields(acronym_overrides: dict, required_field: str) -> None:
    for i, row in enumerate(acronym_overrides["acronyms"]):
        assert required_field in row, f"row {i} missing {required_field!r}: {row}"
        assert isinstance(row[required_field], str)
        assert row[required_field].strip(), f"row {i} has empty {required_field}"


def test_acronyms_are_unique(acronym_overrides: dict) -> None:
    seen: list[str] = []
    for row in acronym_overrides["acronyms"]:
        assert row["acronym"] not in seen, f"duplicate acronym: {row['acronym']}"
        seen.append(row["acronym"])


def test_minimum_row_count(acronym_overrides: dict) -> None:
    """Plan 04-00 spec requires ≥18 acronym rows (the draft ships 21).

    Author may strike ≤3 rows during sign-off (forward-coverage entries
    flagged REMOVABLE in the rationale). Striking more than that requires
    a CONTEXT amendment.
    """
    assert len(acronym_overrides["acronyms"]) >= 18, (
        f"acronym fixture has {len(acronym_overrides['acronyms'])} rows; "
        f"Plan 04-00 requires ≥18 (Wave 0 ship-blocker)"
    )
