"""Wave 0 sign-off ship-blocker for fixtures/chapter_rule_systems.yaml.

Per D-06, the chapter→rule_system mapping is hand-curated by the author.
These tests fail CI if Wave 0 was bypassed (i.e. ``curated_by`` is still
``PENDING_AUTHOR``) or if a new chapter row was added without an
accompanying CONTEXT amendment.

requirements_addressed: TAB-03 (D-06 chapter rule-system map).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ALLOWED_RULE_SYSTEMS = {"FRE", "FRCP", "FRAP", "none", "MRPC"}


@pytest.fixture(scope="module")
def chapter_rule_systems(chapter_rule_systems_path: Path) -> dict:
    """Load the YAML once per module."""
    return yaml.safe_load(chapter_rule_systems_path.read_text(encoding="utf-8"))


def test_yaml_loads(chapter_rule_systems: dict) -> None:
    assert "chapters" in chapter_rule_systems
    assert "metadata" in chapter_rule_systems


def test_chapter_count_is_5(chapter_rule_systems: dict) -> None:
    assert len(chapter_rule_systems["chapters"]) == 5


def test_chapters_numbered_1_to_5(chapter_rule_systems: dict) -> None:
    chapters = sorted(c["chapter"] for c in chapter_rule_systems["chapters"])
    assert chapters == [1, 2, 3, 4, 5]


def test_every_rule_system_in_enum(chapter_rule_systems: dict) -> None:
    for c in chapter_rule_systems["chapters"]:
        assert c["rule_system"] in ALLOWED_RULE_SYSTEMS, (
            f"chapter {c['chapter']} has unknown rule_system={c['rule_system']!r}"
        )


def test_curated_by_is_not_pending(chapter_rule_systems: dict) -> None:
    """Wave 0 sign-off ship-blocker.

    If this fails, Wave 0 (Plan 03B-00) was bypassed. Re-run
    ``/gsd-execute-phase 03B --plan 00`` and complete the
    author-confirmation checkpoint before proceeding.
    """
    curated_by = chapter_rule_systems["metadata"].get("curated_by", "")
    assert curated_by != "PENDING_AUTHOR", (
        "Wave 0 author sign-off NOT received — fixtures/chapter_rule_systems.yaml "
        "still has metadata.curated_by == 'PENDING_AUTHOR'. Run Plan 03B-00 first."
    )


def test_chapter_rule_systems_has_five_chapters(
    chapter_rule_systems: dict,
) -> None:
    """Contract: the chapter-rule-systems fixture declares exactly 5 chapters,
    each with a non-empty title. The cross-check against the section-ground-
    truth fixture (a private asset) lives in the source repo.
    """
    chapters = chapter_rule_systems["chapters"]
    assert len(chapters) == 5
    for c in chapters:
        assert c["title"].strip(), f"chapter row has empty title: {c!r}"
