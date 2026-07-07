"""Tests for the spaCy tokenizer + legal lemma overrides (plan 01-04 Task 4.1).

These exercise the real ``en_core_web_lg`` model. First-run is slow (~2s) due
to model load; module-scoped fixture amortizes across tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from book_indexer.ingest.tokenizer import (
    TokenRecord,
    load_tokenizer,
    nlp_call,
    spacy_model_sha256,
    tokenize_block,
)
from book_indexer.ingest.types import BlockClassification


@pytest.fixture(scope="module")
def nlp_with_overrides():
    return load_tokenizer(Path("config/legal_lemma_overrides.yaml"))


def test_load_tokenizer_registers_phrase_merger(nlp_with_overrides) -> None:
    assert "legal_phrase_merger" in nlp_with_overrides.pipe_names


def test_load_tokenizer_disables_parser_ner_attribute_ruler(nlp_with_overrides) -> None:
    # Per D-17 load-time contract.
    assert "parser" not in nlp_with_overrides.pipe_names
    assert "ner" not in nlp_with_overrides.pipe_names
    assert "attribute_ruler" not in nlp_with_overrides.pipe_names


def test_load_tokenizer_is_cached(nlp_with_overrides) -> None:
    # Same overrides path -> same object (cache hit).
    again = load_tokenizer(Path("config/legal_lemma_overrides.yaml"))
    assert again is nlp_with_overrides


def test_legal_phrase_merged_as_single_token(nlp_with_overrides) -> None:
    doc = nlp_call(nlp_with_overrides, "This case raised res judicata concerns.")
    texts = [t.text for t in doc]
    assert "res judicata" in texts, f"expected merged phrase; got tokens {texts}"


def test_legal_phrase_merge_case_insensitive(nlp_with_overrides) -> None:
    doc = nlp_call(nlp_with_overrides, "Res Judicata applies here.")
    texts = [t.text.lower() for t in doc]
    assert "res judicata" in texts


def test_token_overrides_applied(nlp_with_overrides) -> None:
    doc = nlp_call(nlp_with_overrides, "The media reported on dicta.")
    lemmas = [t.lemma_ for t in doc]
    assert "media" in lemmas, f"expected override lemma 'media'; got {lemmas}"
    assert "dictum" in lemmas, f"expected override lemma 'dictum'; got {lemmas}"


def test_tokenize_block_returns_body_tokens(nlp_with_overrides) -> None:
    block = {
        "type": 0,
        "bbox": (70.0, 100.0, 470.0, 130.0),
        "lines": [{
            "spans": [{
                "text": "The plaintiff asserted res judicata.",
                "size": 10.5,
                "font": "Times-Roman",
                "flags": 0,
            }]
        }],
    }
    classification = BlockClassification(
        pdf_page=42, block_index=0,
        block_type="body", block_role=None,
        avg_font_size=10.5, y_center=115.0,
        bbox=(70.0, 100.0, 470.0, 130.0),
        ambiguity_reason=None,
    )
    records = tokenize_block(nlp_with_overrides, 42, block, classification, starting_token_index=0)
    assert records, "expected at least one token record"
    assert all(isinstance(r, TokenRecord) for r in records)
    assert all(r.pdf_page == 42 for r in records)
    assert [r.token_index for r in records] == list(range(len(records)))
    assert any(r.text == "res judicata" for r in records), (
        f"expected merged phrase token; got {[r.text for r in records]}"
    )
    assert all(r.block_type == "body" for r in records)
    # Font / bbox attribution per-block at Phase 1.
    assert all(r.font_size == 10.5 for r in records)
    assert all(r.font_name == "Times-Roman" for r in records)
    assert all(r.bbox == (70.0, 100.0, 470.0, 130.0) for r in records)


def test_tokenize_block_skips_header_footer(nlp_with_overrides) -> None:
    block = {
        "type": 0,
        "bbox": (70.0, 50.0, 470.0, 80.0),
        "lines": [{"spans": [{"text": "§ 1 MOTION PRACTICE 59", "size": 7.98, "font": "Times-Roman", "flags": 0}]}],
    }
    classification = BlockClassification(
        pdf_page=100, block_index=0,
        block_type="header_footer", block_role="running_head",
        avg_font_size=7.98, y_center=65.0,
        bbox=(70.0, 50.0, 470.0, 80.0),
        ambiguity_reason=None,
    )
    records = tokenize_block(nlp_with_overrides, 100, block, classification, starting_token_index=0)
    assert records == [], f"header_footer should emit no tokens; got {records}"


def test_tokenize_block_skips_image_block(nlp_with_overrides) -> None:
    block = {"type": 0, "bbox": (0.0, 0.0, 10.0, 10.0), "lines": []}
    classification = BlockClassification(
        pdf_page=1, block_index=0,
        block_type="image", block_role="media",
        avg_font_size=None, y_center=5.0,
        bbox=(0.0, 0.0, 10.0, 10.0),
        ambiguity_reason=None,
    )
    records = tokenize_block(nlp_with_overrides, 1, block, classification, starting_token_index=0)
    assert records == []


def test_tokenize_block_handles_empty_spans(nlp_with_overrides) -> None:
    block = {"type": 0, "bbox": (0.0, 0.0, 10.0, 10.0),
             "lines": [{"spans": [{"text": "   ", "size": 10.5, "font": "Times-Roman", "flags": 0}]}]}
    classification = BlockClassification(
        pdf_page=1, block_index=0,
        block_type="body", block_role=None,
        avg_font_size=10.5, y_center=5.0,
        bbox=(0.0, 0.0, 10.0, 10.0),
        ambiguity_reason=None,
    )
    records = tokenize_block(nlp_with_overrides, 1, block, classification, starting_token_index=0)
    assert records == []


def test_tokenize_block_continues_token_index(nlp_with_overrides) -> None:
    block = {
        "type": 0,
        "bbox": (70.0, 100.0, 470.0, 130.0),
        "lines": [{"spans": [{"text": "hello world", "size": 10.5, "font": "Times-Roman", "flags": 0}]}],
    }
    classification = BlockClassification(
        pdf_page=3, block_index=0,
        block_type="body", block_role=None,
        avg_font_size=10.5, y_center=115.0,
        bbox=(70.0, 100.0, 470.0, 130.0),
        ambiguity_reason=None,
    )
    records = tokenize_block(nlp_with_overrides, 3, block, classification, starting_token_index=42)
    assert records[0].token_index == 42
    assert records[-1].token_index == 42 + len(records) - 1


def test_normalize_field_matches_normalizer(nlp_with_overrides) -> None:
    block = {
        "type": 0,
        "bbox": (70.0, 100.0, 470.0, 130.0),
        "lines": [{"spans": [{"text": "The “great” fire.", "size": 10.5, "font": "Times-Roman", "flags": 0}]}],
    }
    classification = BlockClassification(
        pdf_page=1, block_index=0,
        block_type="body", block_role=None,
        avg_font_size=10.5, y_center=115.0,
        bbox=(70.0, 100.0, 470.0, 130.0),
        ambiguity_reason=None,
    )
    records = tokenize_block(nlp_with_overrides, 1, block, classification, starting_token_index=0)
    for r in records:
        # Smart quotes canonicalize to ASCII '"' in the norm field; and norm
        # is always lowercased.
        assert r.norm == r.norm.lower()
        if r.text == '"':
            assert r.norm == '"'


def test_spacy_model_sha256_is_deterministic() -> None:
    # Called twice -> identical digest (content-based hash).
    h1 = spacy_model_sha256()
    h2 = spacy_model_sha256()
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex
