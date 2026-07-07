"""Wave 0 fixture loadability gate: fixtures/render_stopwords.yaml is FINAL.

Per RESEARCH §H-12 'no author sign-off gates' Phase 5 has NO PENDING_AUTHOR
latch. The fixture is grounded in empirical evidence (RESEARCH §H-5's
22-candidate run on the reference corpus). This test asserts loadability + minimum
coverage of the documented stopwords; companion-volume per-volume
overrides happen at Phase 6 CLI time (--stopwords-file flag).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "render_stopwords.yaml"


@pytest.fixture(scope="module")
def stopword_data() -> dict:
    """Load the YAML once per module."""
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_exists() -> None:
    assert FIXTURE.exists(), f"{FIXTURE} missing — Wave 0 incomplete"


def test_fixture_loads(stopword_data: dict) -> None:
    assert isinstance(stopword_data, dict), "top-level must be a mapping"
    assert "metadata" in stopword_data
    assert "stopwords" in stopword_data
    assert isinstance(stopword_data["stopwords"], list)


def test_metadata_curator_set(stopword_data: dict) -> None:
    """Phase 5 has no PENDING_AUTHOR latch (RESEARCH §H-12)."""
    curator = stopword_data["metadata"]["curated_by"]
    assert isinstance(curator, str) and curator, (
        "curated_by must be a non-empty string"
    )
    assert curator != "PENDING_AUTHOR", (
        "Phase 5 stopwords are planner-empirical, not author-curated; "
        "should NOT carry PENDING_AUTHOR per RESEARCH §H-12"
    )


def test_minimum_stopwords_present(stopword_data: dict) -> None:
    """Sanity: the bare-minimum trivially-grouping stems are covered."""
    stopwords = {row["lemma"] for row in stopword_data["stopwords"]}
    # The 5 stems from RESEARCH §H-5 that absolutely must be excluded
    # (otherwise B-06 surfaces 'their' / 'your' / 'other' / 'with' / 'from'
    # as trivially-grouped synthetic main entries).
    required = {"their", "your", "other", "with", "from"}
    missing = required - stopwords
    assert not missing, f"required stopwords missing: {missing}"


def test_minimum_row_count(stopword_data: dict) -> None:
    """Plan 05-00 spec requires ≥25 stopword rows (the draft ships ≥28)."""
    assert len(stopword_data["stopwords"]) >= 25, (
        f"stopword fixture has {len(stopword_data['stopwords'])} rows; "
        f"Plan 05-00 requires ≥25 (Wave 0 ship-blocker)"
    )


@pytest.mark.parametrize("required_field", ["lemma", "reason"])
def test_every_row_has_required_fields(
    stopword_data: dict, required_field: str
) -> None:
    for i, row in enumerate(stopword_data["stopwords"]):
        assert required_field in row, (
            f"row {i} missing {required_field!r}: {row}"
        )
        assert isinstance(row[required_field], str)
        assert row[required_field].strip(), (
            f"row {i} has empty {required_field}"
        )


def test_lemmas_are_unique(stopword_data: dict) -> None:
    seen: list[str] = []
    for row in stopword_data["stopwords"]:
        assert row["lemma"] not in seen, f"duplicate lemma: {row['lemma']}"
        seen.append(row["lemma"])


def test_lemmas_are_lowercase(stopword_data: dict) -> None:
    """B-06 token-lemma comparison is lowercase by construction."""
    for row in stopword_data["stopwords"]:
        assert row["lemma"] == row["lemma"].lower(), (
            f"lemma {row['lemma']!r} is not lowercase — B-06 compares "
            f"with .lower() per RESEARCH §H-5"
        )
