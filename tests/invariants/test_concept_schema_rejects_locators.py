"""CON-07 ship-blocker: the schema rejects all six D-19 fabricated locator shapes.

requirements_addressed: CON-07 (field-wide locator-prefix rejection; expanded
matrix per 03A-CONTEXT.md D-05 / D-18 / D-19; Architecture Lock #2).

Each of the six shapes is parametrized via the ``fabricated_bad_responses``
fixture in ``tests/unit/concepts/conftest.py``. Every shape MUST raise a
Pydantic ``ValidationError``; we ALSO assert the error message cites the
violating field name or the ``locator-prefix forbidden`` marker from
``NoLocatorStr._reject_locator_prefix``.

This test is a CI ship-blocker. Removing any parametrized case or relaxing
the assertion is a Phase 3a release-blocker per CONTEXT D-19.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from book_indexer.concepts import ConceptDiscoveryResponse

pytestmark = pytest.mark.invariants


_SHAPE_ID_TO_ERROR_SUBSTRING: dict[str, tuple[str, ...]] = {
    # Extra field → ``extra_forbidden`` error naming the field.
    "page_int":               ("page",),
    "folio_str":              ("folio",),
    "section_ref_str":        ("section_ref",),
    "pp_list_int":            ("pp",),
    # NoLocatorStr AfterValidator → the error message contains either the
    # field path OR the ``locator-prefix forbidden`` marker.
    "quote_starts_with_glyph":    ("example_quote", "locator-prefix"),
    "variants_item_NdotNN":       ("variants", "locator-prefix"),
}


def test_all_six_shapes_present(fabricated_bad_responses) -> None:
    """Sentinel — prevents accidental emptying of the fixture.

    If someone prunes a payload without updating the test, we notice here
    before the parametrize collects zero cases.
    """
    ids = [shape_id for shape_id, _ in fabricated_bad_responses]
    assert ids == [
        "page_int",
        "folio_str",
        "section_ref_str",
        "quote_starts_with_glyph",
        "variants_item_NdotNN",
        "pp_list_int",
    ], f"D-19 matrix drift: fixture emits {ids}"


@pytest.fixture(params=range(6))
def bad_shape(request, fabricated_bad_responses) -> tuple[str, dict]:
    return fabricated_bad_responses[request.param]


def test_schema_rejects_fabricated_locator_shape(bad_shape) -> None:
    """Every D-19 fabricated shape raises ValidationError.

    The error must either (a) name the violating extra field or (b) mention
    the ``locator-prefix forbidden`` marker (for NoLocatorStr rejections).
    """
    shape_id, payload = bad_shape
    expected_substrings = _SHAPE_ID_TO_ERROR_SUBSTRING[shape_id]

    with pytest.raises(ValidationError) as exc_info:
        ConceptDiscoveryResponse.model_validate(payload)

    error_text = str(exc_info.value)
    # At least one of the expected substrings must appear in the error text.
    assert any(sub in error_text for sub in expected_substrings), (
        f"shape {shape_id!r} raised ValidationError but the error text did "
        f"not cite any of {expected_substrings!r}.\n\n"
        f"Error text:\n{error_text}"
    )
