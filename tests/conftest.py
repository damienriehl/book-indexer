"""Shared fixtures for the book_indexer test suite (public-asset tier).

Every fixture here is deterministic: environment is frozen, paths are absolute,
YAML/fixture files are loaded once per test session.

This is the public-asset adaptation of the source repo's tests/conftest.py.
All private-corpus-specific fixtures (the full 259-page corpus, folio/section
ground truth, artifacts/ corpus connection) have been removed; only
self-contained fixtures
that rely on synthetic corpora, spaCy, or the committed public fixtures remain.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, settings

# Hypothesis profile registration.
# "ci" → derandomize=True pins the RNG seed per-example so CI runs produce
# byte-identical Hypothesis shrinking traces; suppress the function-scoped
# fixture health check because the property tests legitimately use
# function-scoped synthetic corpora (D-01 strategy).
settings.register_profile(
    "ci",
    derandomize=True,
    max_examples=500,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.register_profile(
    "dev",
    max_examples=100,
    deadline=500,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

# Resolve repo root from this file's location.
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures"
SAMPLES_DIR = REPO_ROOT / "samples"
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def sample_pdf_path() -> Path:
    """10-page synthetic treatise PDF for fast unit tests (public-domain asset)."""
    p = SAMPLES_DIR / "synthetic_treatise.pdf"
    assert p.exists(), f"Missing {p}"
    return p


@pytest.fixture(scope="session")
def legal_lemma_overrides() -> dict[str, Any]:
    p = CONFIG_DIR / "legal_lemma_overrides.yaml"
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(autouse=True)
def frozen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure determinism env vars are set for every test."""
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")


# ===========================================================================
# Shared property/invariant infrastructure.
# ===========================================================================

_VOCAB_PATH = FIXTURES_DIR / "legal_phrase_vocab.yaml"


def _load_vocab() -> list[str]:
    with _VOCAB_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [row["term"] for row in data["phrases"]]


LEGAL_PHRASE_VOCAB: list[str] = _load_vocab()
assert LEGAL_PHRASE_VOCAB and LEGAL_PHRASE_VOCAB[0] == "voir dire", (
    "legal_phrase_vocab.yaml[0] must be 'voir dire' for Hypothesis "
    "shrink-friendliness (D-11). See the fixture integrity test."
)

# Known-absent sentinels for negative-path properties. D-02: curated list
# instead of free-form text() + assume() to avoid filter_too_much health-checks.
KNOWN_ABSENT_TERMS: list[str] = [
    "xyzzy_sentinel_42",
    "DEFINITELY_NOT_IN_CORPUS",
    "qqqqq zzzzz yyyyy",
    "completely_invented_phrase_bxyz",
]


@pytest.fixture(scope="session", autouse=True)
def _warm_spacy_pipeline() -> None:
    """Pay spaCy's cold-start cost once before Hypothesis begins running
    examples; keeps the steady-state 500ms deadline realistic.

    ``tokenize_query`` invokes ``spacy.load('en_core_web_lg')`` on first call,
    which takes ~600-900ms (model load + legal-phrase-merger registration).
    After the first call the model is cached and subsequent calls complete in
    ~3ms. This autouse fixture pre-warms the cache at session start so every
    @given example runs at steady-state speed.
    """
    from book_indexer.verify.query_tokenizer import tokenize_query

    # "voir dire" exercises the legal-phrase-merger ContextVar (single-token
    # merged output) so the tokenizer factory is fully initialized.
    tokenize_query("voir dire")


@pytest.fixture(scope="session")
def nlp():
    """Session-scoped spaCy en_core_web_lg."""
    import spacy
    return spacy.load("en_core_web_lg")


@pytest.fixture(scope="session")
def nlp_with_doctrinal(nlp):
    """Session-scoped nlp + EntityRuler from fixtures/doctrinal_patterns.yaml."""
    from book_indexer.concepts.symbolic import build_doctrinal_nlp
    return build_doctrinal_nlp(nlp)


# Synthetic-corpus builder — factory fixture


@dataclass
class SyntheticToken:
    pdf_page: int
    token_index: int
    text: str
    norm: str
    lemma: str
    block_type: str = "body"
    section_id: int | None = 3  # default deepest section


@dataclass
class SyntheticSection:
    section_id: int
    section_ref: str  # "§1", "§1.01", "§1.01.1", "Chapter N"
    section_level: int  # 0 | 1 | 2 | 3
    parent_id: int | None
    start_pdf_page: int
    start_token_offset: int
    end_pdf_page: int
    end_token_offset: int
    chapter: int = 1
    title: str = "Synthetic Section"


@dataclass
class SyntheticSpec:
    pages: dict[int, str] = field(default_factory=dict)
    sections: list[SyntheticSection] = field(default_factory=list)
    tokens: list[SyntheticToken] = field(default_factory=list)


def _build_synthetic(spec: SyntheticSpec) -> sqlite3.Connection:
    """Materialize a SyntheticSpec as a fresh in-memory SQLite corpus.

    Uses the authoritative ``_DDL`` from Phase 1's corpus_writer so the
    schema matches production exactly — no hand-written DDL drift.
    """
    from book_indexer.ingest.corpus_writer import _DDL

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(_DDL)

    for pdf_page, folio in sorted(spec.pages.items()):
        conn.execute(
            "INSERT INTO pages (pdf_page, folio, folio_style, folio_tier, "
            "page_section, active_chapter_section, section, bbox_page, block_count) "
            "VALUES (?, ?, 'arabic', 'tier2', NULL, NULL, 'body', '[0,0,540,720]', 1)",
            (pdf_page, folio),
        )

    for s in spec.sections:
        conn.execute(
            "INSERT INTO sections (section_id, section_ref, global_id, section_level, "
            "chapter, parent_id, title, start_pdf_page, start_token_offset, "
            "end_pdf_page, end_token_offset, start_folio) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0')",
            (s.section_id, s.section_ref, f"gid-{s.section_id}",
             s.section_level, s.chapter, s.parent_id, s.title,
             s.start_pdf_page, s.start_token_offset,
             s.end_pdf_page, s.end_token_offset),
        )

    for t in spec.tokens:
        conn.execute(
            "INSERT INTO tokens (pdf_page, token_index, text, norm, lemma, block_type, "
            "block_role, bbox, font_size, font_name, crosses_page_break, section_id) "
            "VALUES (?, ?, ?, ?, ?, ?, NULL, '[0,0,0,0]', 10.98, 'CS', 0, ?)",
            (t.pdf_page, t.token_index, t.text, t.norm, t.lemma,
             t.block_type, t.section_id),
        )
    return conn


@pytest.fixture
def synthetic_corpus_builder() -> Callable[[SyntheticSpec], sqlite3.Connection]:
    """Factory fixture: callers build a SyntheticSpec and get a fresh
    in-memory sqlite3.Connection for each Hypothesis example."""
    return _build_synthetic


@pytest.fixture
def make_simple_phrase_corpus(
    synthetic_corpus_builder: Callable[[SyntheticSpec], sqlite3.Connection],
) -> Callable[..., tuple[sqlite3.Connection, int, tuple[str, ...]]]:
    """High-level helper for D-07/D-08 properties + evidence-ledger tests.

    Given a phrase, builds a 1-page, 3-section (Chapter -> §1 -> §1.01)
    synthetic corpus with ``phrase``'s normalized tokens at offset 3,
    surrounded by enough filler that the ±30-token snippet window produces
    a ≥60-char ``verbatim_snippet``.

    Returns ``(conn, deepest_section_id, section_path_expected)``.

    The phrase is tokenized through the SAME pipeline the verifier uses
    (``query_tokenizer.tokenize_query``) so multi-word phrases like
    "voir dire" are inserted as the single merged token that Phase 1's
    legal-phrase-merger would produce.
    """
    from book_indexer.verify.query_tokenizer import tokenize_query

    def _factory(
        phrase: str, folio: str = "42"
    ) -> tuple[sqlite3.Connection, int, tuple[str, ...]]:
        qs = tokenize_query(phrase)
        filler_before = ["The", "court", "addressed"]
        filler_after = [
            "in", "considerable", "detail", "notwithstanding", "prior",
            "authority", "establishing", "a", "contrary", "rule", "for",
            "the", "selection", "of", "the", "jury", "panel", "under",
            "established", "precedent",
        ]
        prose: list[tuple[str, str, str]] = []
        for w in filler_before:
            prose.append((w, w.lower(), w.lower()))
        for q in qs:
            prose.append((q.norm, q.norm, q.lemma))
        for w in filler_after:
            prose.append((w, w.lower(), w.lower()))

        spec = SyntheticSpec(
            pages={1: folio},
            sections=[
                SyntheticSection(1, "Chapter 1", 0, None, 1, 0, 1, 999, title="Chapter"),
                SyntheticSection(2, "§1", 1, 1, 1, 0, 1, 999),
                SyntheticSection(3, "§1.01", 2, 2, 1, 0, 1, 999, title="Voir Dire"),
            ],
            tokens=[
                SyntheticToken(pdf_page=1, token_index=i, text=text, norm=norm,
                               lemma=lemma, section_id=3)
                for i, (text, norm, lemma) in enumerate(prose)
            ],
        )
        return _build_synthetic(spec), 3, ("§1", "§1.01")

    return _factory
