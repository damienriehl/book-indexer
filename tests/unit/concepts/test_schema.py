"""Unit tests for ``src/book_indexer/concepts/schema.py``.

Happy-path validation, frozen-ness, ``extra="forbid"``, H-8 envelope
independence, and schema-emission assertions. The negative-path ship-blocker
lives under ``tests/invariants/test_concept_schema_rejects_locators.py``.

requirements_addressed: CON-02, CON-07
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from book_indexer.concepts import (
    ClaudeCliEnvelope,
    ConceptCandidate,
    ConceptDiscoveryResponse,
    build_json_schema,
)


def _valid_candidate_kwargs() -> dict:
    return {
        "term": "hearsay",
        "canonical_form": "hearsay",
        "variants": [],
        "example_quote": "An out-of-court statement offered for the truth.",
    }


def test_concept_candidate_happy_path() -> None:
    cand = ConceptCandidate(**_valid_candidate_kwargs())
    assert cand.term == "hearsay"
    # Frozen ⇒ Pydantic may coerce list to tuple; accept either container
    # as long as it's empty.
    assert list(cand.variants) == []
    assert cand.kind is None
    assert cand.suggested_subentries is None


def test_concept_candidate_all_optional_fields() -> None:
    cand = ConceptCandidate(
        **_valid_candidate_kwargs(),
        kind="doctrine",
        suggested_subentries=["business records exception"],
    )
    assert cand.kind == "doctrine"
    assert list(cand.suggested_subentries or []) == ["business records exception"]


def test_concept_candidate_frozen() -> None:
    cand = ConceptCandidate(**_valid_candidate_kwargs())
    with pytest.raises((ValidationError, TypeError, AttributeError)):
        cand.term = "perjury"  # type: ignore[misc]


def test_concept_candidate_extra_forbid() -> None:
    kwargs = {**_valid_candidate_kwargs(), "unexpected_field": 42}
    with pytest.raises(ValidationError) as exc:
        ConceptCandidate(**kwargs)  # type: ignore[arg-type]
    assert "unexpected_field" in str(exc.value)


def test_concept_candidate_term_min_length() -> None:
    kwargs = {**_valid_candidate_kwargs(), "term": "h"}
    with pytest.raises(ValidationError) as exc:
        ConceptCandidate(**kwargs)
    assert "term" in str(exc.value)


def test_concept_candidate_variants_max_length_11() -> None:
    kwargs = {**_valid_candidate_kwargs(), "variants": [f"v{i}" for i in range(11)]}
    with pytest.raises(ValidationError) as exc:
        ConceptCandidate(**kwargs)
    assert "variants" in str(exc.value)


def test_concept_candidate_rejects_section_prefix_in_term() -> None:
    """CON-07 / D-05 field-wide regex on term."""
    kwargs = {**_valid_candidate_kwargs(), "term": "§ 2.04"}
    with pytest.raises(ValidationError) as exc:
        ConceptCandidate(**kwargs)
    assert "locator-prefix" in str(exc.value) or "term" in str(exc.value)


def test_concept_candidate_rejects_NdotNN_in_variant_item() -> None:
    """H-3: list-item validator via ``list[NoLocatorStr]`` item-type, not wrapping validator."""
    kwargs = {**_valid_candidate_kwargs(), "variants": ["1.05"]}
    with pytest.raises(ValidationError) as exc:
        ConceptCandidate(**kwargs)
    assert "variants" in str(exc.value) or "locator-prefix" in str(exc.value)


def test_concept_discovery_response_happy_path() -> None:
    envelope = ConceptDiscoveryResponse.model_validate({
        "schema_version": "1",
        "pass_type": "doctrinal",
        "chunk_id": "ch1",
        "candidates": [_valid_candidate_kwargs()],
    })
    assert envelope.pass_type == "doctrinal"
    assert envelope.chunk_id == "ch1"
    assert len(envelope.candidates) == 1


def test_concept_discovery_response_chunk_id_pattern() -> None:
    """``chunk_id`` must match ``^ch\\d+$`` — accepts ch1..ch999; rejects ``chunkA``."""
    good = {
        "schema_version": "1",
        "pass_type": "doctrinal",
        "chunk_id": "ch5",
        "candidates": [],
    }
    ConceptDiscoveryResponse.model_validate(good)  # should not raise

    bad = {**good, "chunk_id": "chunkA"}
    with pytest.raises(ValidationError) as exc:
        ConceptDiscoveryResponse.model_validate(bad)
    assert "chunk_id" in str(exc.value)


def test_concept_discovery_response_pass_type_enum() -> None:
    payload = {
        "schema_version": "1",
        "pass_type": "other",  # not in the 4 literals
        "chunk_id": "ch1",
        "candidates": [],
    }
    with pytest.raises(ValidationError) as exc:
        ConceptDiscoveryResponse.model_validate(payload)
    assert "pass_type" in str(exc.value)


def test_claude_cli_envelope_h8_is_error_independence() -> None:
    """H-8: is_error and structured_output are independent.

    All four corners of the 2x2 are valid envelopes (Pydantic-wise); downstream
    code uses ``SubagentCliError`` to translate problematic combinations into
    Phase 3a exceptions — but the envelope schema itself accepts everything.
    """
    for is_error, so in [(False, None), (False, {"candidates": []}),
                         (True, None), (True, {"candidates": []})]:
        env = ClaudeCliEnvelope(is_error=is_error, structured_output=so)  # type: ignore[arg-type]
        assert env.is_error is is_error
        assert env.structured_output == so


def test_build_json_schema_emits_lookahead_pattern() -> None:
    """H-2: the NoLocatorStr lookahead pattern reaches the wire-level schema."""
    schema = build_json_schema()
    assert schema["additionalProperties"] is False
    # The pattern appears on at least one field (e.g. term) in the $defs.
    defs = schema.get("$defs", {})
    assert "ConceptCandidate" in defs
    term_prop = defs["ConceptCandidate"]["properties"]["term"]
    # Pydantic nests json_schema_extra into the property; the exact path can be
    # ``term_prop["pattern"]`` or via allOf/anyOf. Search the term node.
    term_serialized = json.dumps(term_prop)
    assert (
        "(?!§|\\\\d{1,2}\\\\.\\\\d{2})" in term_serialized
        or "(?!§|\\d{1,2}\\.\\d{2})" in term_serialized
        or "(?!\\u00a7|\\\\d{1,2}\\\\.\\\\d{2})" in term_serialized
        or "(?!\\u00a7|\\d{1,2}\\.\\d{2})" in term_serialized
    ), f"No lookahead pattern found in term schema: {term_serialized}"
