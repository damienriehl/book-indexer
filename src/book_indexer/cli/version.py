"""``pipeline_version`` helpers (read from ``pyproject.toml``).

CONTEXT 06 D-09: ``pyproject.toml`` ``[project].version`` is the single source
of truth for ``pipeline_version``. Phase 5's ``metadata.json`` reads from this
helper at build time. Plan 06-05 will bump 0.1.0 → 1.0.0 at the v1.0 release.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


def read_pipeline_version() -> str:
    """Read ``[project].version`` from ``pyproject.toml``.

    Single source of truth per D-09. NOT meant to be try/excepted; callers
    rely on a non-empty version string. Raises:

    * ``FileNotFoundError`` — if pyproject.toml is missing (broken install).
    * ``KeyError`` — if ``[project].version`` is empty / absent.
    """
    if not PYPROJECT_PATH.exists():
        raise FileNotFoundError(f"pyproject.toml not found at {PYPROJECT_PATH}")
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not version:
        raise KeyError("pyproject.toml [project].version is empty")
    return version
