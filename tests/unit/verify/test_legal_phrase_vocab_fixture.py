"""requirements_addressed: VER-05 (fixture backing); CONTEXT D-06, D-11

Integrity test for fixtures/legal_phrase_vocab.yaml — mirrors
tests/unit/test_folio_fixture_integrity.py.
"""
from __future__ import annotations

from pathlib import Path

import yaml

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"
VOCAB_PATH = FIXTURES_DIR / "legal_phrase_vocab.yaml"

_ALLOWED_CATEGORIES = {"doctrine", "rule", "case", "procedure", "latin"}


def _load() -> dict:
    with VOCAB_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_vocab_file_exists() -> None:
    assert VOCAB_PATH.is_file(), f"missing {VOCAB_PATH}"


def test_vocab_has_required_top_level_keys() -> None:
    data = _load()
    assert set(data.keys()) >= {"version", "metadata", "phrases"}
    assert data["version"] == 1


def test_vocab_has_at_least_40_phrases() -> None:
    """D-06: seed list has 40 entries; fixture must not shrink below that."""
    data = _load()
    assert len(data["phrases"]) >= 40


def test_every_phrase_has_required_keys() -> None:
    for i, row in enumerate(_load()["phrases"]):
        assert set(row.keys()) >= {"term", "category", "expected_min_hits"}, (
            f"phrase[{i}] missing keys: {row}"
        )
        assert row["category"] in _ALLOWED_CATEGORIES, (
            f"phrase[{i}] has disallowed category {row['category']!r}; "
            f"allowed: {_ALLOWED_CATEGORIES}"
        )
        assert isinstance(row["expected_min_hits"], int) and row["expected_min_hits"] >= 1


def test_first_phrase_is_voir_dire() -> None:
    """D-11 shrink-friendliness: sampled_from shrinks toward index 0, so
    index 0 MUST be a well-studied phrase with obvious readable failures."""
    data = _load()
    assert data["phrases"][0]["term"] == "voir dire"


def test_phrases_are_unique() -> None:
    terms = [row["term"] for row in _load()["phrases"]]
    assert len(terms) == len(set(terms)), (
        f"duplicate phrases: {[t for t in terms if terms.count(t) > 1]}"
    )
