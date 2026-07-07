"""Lock #2 ship-blocker — curator LLM-proposal schemas reject locator-shaped fields.

For every forbidden field name in the matrix, both ``RemovalsResponse`` and
``RecapitalizationsResponse`` MUST raise ``ValidationError`` (via
``extra="forbid"``). This is the Phase 7 mirror of Phase 3a's
``test_concept_schema_rejects_locators.py``.

Lock #2 confirmation: only the LLM-output schemas are gated here.
``CuratorOverrides`` (the YAML curator-only schema) is NOT consumed by the
LLM — it's the human-curated fixture container; ``keep_plural_variants:``
and the other curator-only fields live there. Lock #2 applies strictly to
the JSON Schemas the LLM sees via ``--json-schema``.

requirements_addressed: CUR-01, CUR-02, CUR-03 (LLM-proposal Lock #2 surface).
Architecture Lock #2 enforced.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from book_indexer.curator import (
    RecapitalizationsResponse,
    RemovalsResponse,
)

pytestmark = pytest.mark.invariants


_FORBIDDEN_FIELDS = ("page", "pdf_page", "section_ref", "folio", "pp", "p")


@pytest.mark.parametrize("forbidden", _FORBIDDEN_FIELDS)
def test_removals_response_rejects_locator_field(forbidden: str) -> None:
    """RemovalsResponse rejects every locator-shaped extra field."""
    payload = {
        "removals": [
            {"term": "x", "reason": "y", forbidden: 1},
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        RemovalsResponse.model_validate(payload)
    err = str(exc_info.value)
    assert forbidden in err or "extra" in err.lower(), (
        f"forbidden field {forbidden!r} did not surface in error text:\n{err}"
    )


@pytest.mark.parametrize("forbidden", _FORBIDDEN_FIELDS)
def test_recapitalizations_response_rejects_locator_field(forbidden: str) -> None:
    """RecapitalizationsResponse rejects every locator-shaped extra field."""
    payload = {
        "recapitalizations": [
            {"wrong": "x", "right": "X", "reason": "y", forbidden: 1},
        ],
    }
    with pytest.raises(ValidationError) as exc_info:
        RecapitalizationsResponse.model_validate(payload)
    err = str(exc_info.value)
    assert forbidden in err or "extra" in err.lower(), (
        f"forbidden field {forbidden!r} did not surface in error text:\n{err}"
    )


def test_top_level_extra_forbidden_in_both_responses() -> None:
    """Top-level extra (outside the list items) also forbidden."""
    with pytest.raises(ValidationError):
        RemovalsResponse.model_validate({"removals": [], "page": 1})
    with pytest.raises(ValidationError):
        RecapitalizationsResponse.model_validate(
            {"recapitalizations": [], "section_ref": "§ 1.01"}
        )
