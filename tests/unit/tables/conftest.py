"""Shared fixtures for tests/unit/tables/."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Repo root, computed from this file's location."""
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def fixtures_dir(repo_root: Path) -> Path:
    return repo_root / "fixtures"


@pytest.fixture(scope="session")
def chapter_rule_systems_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "chapter_rule_systems.yaml"


@pytest.fixture(scope="session")
def citation_jurisdictions_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "citation_jurisdictions.yaml"
