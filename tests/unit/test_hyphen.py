"""Tests for the hyphen-rejoin validator (plan 01-04 Task 4.1, D-14, D-16).

Uses a real ``en_core_web_lg`` load (lemmatizer / parser / ner / attribute_ruler
disabled) because ``is_rejoin_valid`` queries ``Lexeme.is_oov``; a blank
language has no vocabulary and would fail the positive-case assertions.
"""
from __future__ import annotations

import pytest
import spacy

from book_indexer.ingest.hyphen import is_rejoin_valid, should_rejoin_line_end


@pytest.fixture(scope="module")
def nlp():
    return spacy.load(
        "en_core_web_lg",
        disable=["parser", "ner", "attribute_ruler", "lemmatizer", "tagger"],
    )


def test_is_rejoin_valid_for_known_word(nlp) -> None:
    # "evidence" should be in spaCy's vocab (is_oov == False)
    assert is_rejoin_valid("evi", "dence", nlp, overrides=set()) is True


def test_is_rejoin_valid_false_for_nonword(nlp) -> None:
    # "crossexamination" should NOT be in vocab (hyphenated compound preserved)
    assert is_rejoin_valid("cross", "examination", nlp, overrides=set()) is False


def test_is_rejoin_valid_via_override(nlp) -> None:
    overrides = {"resjudicata"}
    assert is_rejoin_valid("res", "judicata", nlp, overrides=overrides) is True


def test_is_rejoin_valid_empty_concat_is_false(nlp) -> None:
    # Defensive: empty concatenation is never a valid rejoin.
    assert is_rejoin_valid("", "", nlp, overrides=set()) is False


def test_is_rejoin_valid_is_case_insensitive(nlp) -> None:
    # Capitalization shouldn't change the vocabulary decision.
    assert is_rejoin_valid("Evi", "Dence", nlp, overrides=set()) is True


def test_should_rejoin_line_end_detects_hyphen() -> None:
    ok, stem = should_rejoin_line_end("The quick cross-", "examination was")
    assert ok is True
    assert stem == "cross"


def test_should_rejoin_line_end_rejects_non_hyphen() -> None:
    ok, stem = should_rejoin_line_end("The quick brown fox", "jumped")
    assert ok is False
    assert stem is None


def test_should_rejoin_line_end_rejects_bare_hyphen() -> None:
    # "- - -" has no letter directly before the trailing hyphen.
    ok, stem = should_rejoin_line_end("- - -", "more")
    assert ok is False


def test_should_rejoin_line_end_trims_trailing_whitespace() -> None:
    # Right-strip should surface a hyphen hiding behind trailing whitespace.
    ok, stem = should_rejoin_line_end("The witness saw evi-  ", "dence")
    assert ok is True
    assert stem == "evi"


def test_should_rejoin_line_end_rejects_short_input() -> None:
    # Too short to host a stem + hyphen.
    ok, stem = should_rejoin_line_end("-", "x")
    assert ok is False
    assert stem is None
