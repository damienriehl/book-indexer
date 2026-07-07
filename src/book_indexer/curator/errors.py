"""Curator-package typed exceptions.

requirements_addressed: CUR-01, CUR-02, CUR-03 (curator-pass error contract).
"""
from __future__ import annotations


class CuratorFixtureError(Exception):
    """Raised when ``fixtures/index_curator_overrides.yaml`` fails the curator
    gate (``metadata.curated_by == "PENDING_AUTHOR"``) or fails Pydantic
    validation. Build-blocker — every caller MUST surface this; no silent
    fallback to "no overrides".
    """


class RecapitalizeGuardError(Exception):
    """Raised when a ``(wrong, right)`` pair fails the strict
    ``wrong.lower() == right.lower()`` guard (CUR-02). Any letter change
    beyond case is a hard build failure — prevents LLM hallucination from
    sneaking through under a "capitalization fix" pretense.
    """
