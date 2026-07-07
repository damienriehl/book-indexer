"""Shared fixtures for render unit tests.

Includes synthetic IndexTree / IndexEntry / Locator factories plus a
``frozen_metadata`` fixture for AUD-04 testing.
"""
from __future__ import annotations

import pytest

from book_indexer.render import (
    IndexEntry,
    IndexTreeProvenance,
    Locator,
    Metadata,
    SubEntry,
)


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
def make_entry(make_locator):
    """Factory returning a synthetic IndexEntry with sensible defaults."""

    def _make(
        canonical: str = "hearsay rule",
        id: str | None = None,
        variants: list[str] | None = None,
        sub_entries: list[SubEntry] | None = None,
        locators: list[Locator] | None = None,
        derived_from_table: str | None = None,
    ) -> IndexEntry:
        return IndexEntry(
            id=id or canonical.lower().replace(" ", "-"),
            canonical=canonical,
            sort_key=canonical.lower(),
            derived_from_table=derived_from_table,
            locators=locators or [make_locator()],
            sub_entries=sub_entries or [],
            see=[],
            see_also=[],
            variants=variants or [],
        )

    return _make


@pytest.fixture
def frozen_metadata() -> Metadata:
    """A canonical Metadata instance for invariant tests.

    All version pins are realistic (taken from the live provenance
    files at Phase 5 research time). built_at is the Lock #5 sentinel.
    """
    return Metadata(
        pdf_sha256="94503c16dcc3ce29d64be1591fec85eb94b83c4452622cfd9676bb604e553070",
        pipeline_version="0.1.0",
        index_tree_schema_version="1.0",
        eyecite_version="2.7.6",
        reporters_db_version="3.2.64",
        courts_db_version="0.10.27",
        spacy_version="3.8.14",
        spacy_model_sha="ffa95b0677e0e23e4f656c4916805de0595ebcf58fc1d7bddd0691a1cc04a521",
        pymupdf_version="1.27.2.2",
        python_docx_version="1.2.0",
        cli_version="2.1.119",
    )


@pytest.fixture
def make_provenance():
    """Factory returning a synthetic IndexTreeProvenance with sensible defaults."""

    def _make(**overrides) -> IndexTreeProvenance:
        base = dict(
            spacy_version="3.8.14",
            spacy_model_sha="ffa95b06",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.64",
            courts_db_version="0.10.27",
            pdf_sha256="94503c16",
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
