"""Shared fixtures for assembly unit tests."""
from __future__ import annotations

import pytest

from book_indexer.assembly import (
    IndexEntry,
    IndexTree,
    IndexTreeProvenance,
    Locator,
    SubEntry,
)
from book_indexer.verify.evidence import Evidence


@pytest.fixture
def make_evidence():
    """Factory returning a synthetic Evidence row.

    Honors Phase 2 validators:
      * ``len(section_path) == section_level``
      * ``section_path[-1] == section_ref``
      * ``verbatim_snippet`` length >= 60
    Caller passes ``section_path`` (tuple); ``section_ref`` and
    ``section_level`` are derived to satisfy the cross-field validator.
    """

    def _make(
        section_path: tuple[str, ...],
        *,
        canonical_term: str = "voir dire",
        matched_variant: str = "voir dire",
        folio: str | None = None,
        pdf_page: int = 10,
        token_offset: int = 0,
        match_mode: str = "lemma",
        verbatim_snippet: str | None = None,
    ) -> Evidence:
        if not section_path:
            raise ValueError("section_path must be non-empty")
        section_ref = section_path[-1]
        section_level = len(section_path)
        if folio is None:
            # Deterministic from pdf_page so tests can predict output folios
            folio = str(pdf_page)
        if verbatim_snippet is None:
            verbatim_snippet = (
                "lorem ipsum dolor sit amet consectetur adipiscing elit "
                "sed do eiusmod tempor incididunt ut labore et dolore magna"
            )
        return Evidence(
            canonical_term=canonical_term,
            matched_variant=matched_variant,
            section_ref=section_ref,
            section_level=section_level,
            section_path=section_path,
            folio=folio,
            pdf_page=pdf_page,
            token_offset=token_offset,
            match_mode=match_mode,
            verbatim_snippet=verbatim_snippet,
        )

    return _make


@pytest.fixture
def make_locator():
    """Factory returning a synthetic Locator with sensible defaults."""

    def _make(
        section_ref: str = "§2.04",
        folio: str = "78",
        evidence_id: int = 1,
    ) -> Locator:
        return Locator(section_ref=section_ref, folio=folio, evidence_id=evidence_id)

    return _make


@pytest.fixture
def make_provenance():
    """Factory returning a synthetic IndexTreeProvenance with sensible defaults."""

    def _make(**overrides) -> IndexTreeProvenance:
        base = dict(
            spacy_version="3.8.14",
            spacy_model_sha="abc123",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.64",
            courts_db_version="0.10.27",
            pdf_sha256="94503c16dcc3ce29d64be1591fec85eb94b83c4452622cfd9676bb604e553070",
            corpus_sha="cafebabe",
            concepts_sha={},
            tables_sha={},
            pre_dedup_count=0,
            post_dedup_count=0,
            post_deconflict_count=0,
            post_zero_evidence_count=0,
            oversize_parent_count=0,
            sub_entry_total_count=0,
            oob_status="none",
            max_sub_entries_per_parent=0,
            parents_with_no_locators=0,
            dropped_table_citations=[],
            zero_evidence_drops=[],
            slug_collision_count=0,
            iteration_depth=1,
        )
        base.update(overrides)
        return IndexTreeProvenance(**base)

    return _make


@pytest.fixture
def make_index_entry(make_locator):
    """Factory returning a minimal valid IndexEntry."""

    def _make(**overrides) -> IndexEntry:
        base = dict(
            id="voir-dire",
            canonical="voir dire",
            sort_key="voir dire",
            derived_from_table=None,
            locators=[make_locator()],
            sub_entries=[],
            see=[],
            see_also=[],
            variants=[],
        )
        base.update(overrides)
        return IndexEntry(**base)

    return _make


@pytest.fixture
def make_sub_entry(make_locator):
    """Factory returning a minimal valid SubEntry."""

    def _make(**overrides) -> SubEntry:
        base = dict(
            text="examination of jurors",
            sort_key="examination of jurors",
            locators=[make_locator()],
        )
        base.update(overrides)
        return SubEntry(**base)

    return _make


@pytest.fixture
def make_index_tree(make_provenance, make_index_entry):
    """Factory returning a minimal valid IndexTree."""

    def _make(entries=None, **overrides) -> IndexTree:
        base = dict(
            schema_version="1",
            provenance=make_provenance(),
            entries=entries if entries is not None else [make_index_entry()],
        )
        base.update(overrides)
        return IndexTree(**base)

    return _make
