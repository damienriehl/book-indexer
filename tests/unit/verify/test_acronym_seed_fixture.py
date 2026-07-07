"""requirements_addressed: VER-02 (fixture backing)

Integrity test for fixtures/acronym_seed.yaml — used by the acronym-mode
matcher (Plan 02-02) and acronym-mode Hypothesis property (Plan 02-03).
"""
from __future__ import annotations

from pathlib import Path

import yaml

ACRONYM_PATH = Path(__file__).resolve().parents[3] / "fixtures" / "acronym_seed.yaml"


def _load() -> dict:
    with ACRONYM_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_acronym_file_exists() -> None:
    assert ACRONYM_PATH.is_file(), f"missing {ACRONYM_PATH}"


def test_has_at_least_5_pairs() -> None:
    assert len(_load()["pairs"]) >= 5


def test_every_pair_has_canonical_and_acronyms() -> None:
    for i, row in enumerate(_load()["pairs"]):
        assert "canonical" in row and "acronyms" in row, f"pair[{i}]={row}"
        assert isinstance(row["canonical"], str) and row["canonical"]
        assert isinstance(row["acronyms"], list) and row["acronyms"]
        for a in row["acronyms"]:
            assert isinstance(a, str) and a, f"pair[{i}] has empty acronym"


def test_fre_acronym_pair_exists() -> None:
    """Sanity check: FRE↔Federal Rules of Evidence pair is present — this
    is the canonical acronym example used in multiple property tests."""
    pairs = _load()["pairs"]
    fre = [p for p in pairs if "FRE" in p["acronyms"]]
    assert fre, "missing FRE canonical-pair"
    assert "Federal Rules of Evidence" in fre[0]["canonical"]
