"""Domain-configuration loader for book-indexer (``book.toml``).

This module reads the *domain* config (which book, which section/folio
fixtures, which citation-tables plugin) that is intentionally kept out of
``pyproject.toml`` (package config). See ``book.toml`` at the repo root for
the documented schema.

The loader is dependency-light (stdlib ``tomllib`` only) and fail-soft: a
missing ``book.toml`` yields an all-defaults :class:`BookConfig`, so unit
tests and ad-hoc invocations work without any configuration file.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_HEADING_REGEX = r"^(?:§\s*\d+(?:\.\d+)*|Chapter\s+\d+)\b"


@dataclass(frozen=True)
class BookConfig:
    """Parsed ``book.toml`` domain configuration."""

    title: str = "Untitled"
    source_pdf: Path | None = None
    section_fixture: Path | None = None
    heading_regex: str = DEFAULT_HEADING_REGEX
    folio_fixture: Path | None = None
    lemma_overrides: Path | None = None
    tables_plugin: str = "legal-citations"
    tables_jurisdictions: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def _opt_path(base: Path, value: str | None) -> Path | None:
    """Resolve a possibly-empty relative path against ``base``; empty → None."""
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (base / p)


def load_book_config(path: str | Path | None = None) -> BookConfig:
    """Load ``book.toml`` from *path* (or the caller's working tree).

    Search order when *path* is None: ``./book.toml``. A missing file returns
    an all-defaults :class:`BookConfig`.
    """
    toml_path = Path(path) if path is not None else Path("book.toml")
    if not toml_path.is_file():
        return BookConfig()

    base = toml_path.resolve().parent
    with toml_path.open("rb") as fh:
        data = tomllib.load(fh)

    book = data.get("book", {})
    sections = data.get("sections", {})
    folios = data.get("folios", {})
    lemma = data.get("lemma", {})
    tables = data.get("tables", {})

    return BookConfig(
        title=book.get("title", "Untitled"),
        source_pdf=_opt_path(base, book.get("source_pdf")),
        section_fixture=_opt_path(base, sections.get("fixture")),
        heading_regex=sections.get("heading_regex") or DEFAULT_HEADING_REGEX,
        folio_fixture=_opt_path(base, folios.get("fixture")),
        lemma_overrides=_opt_path(base, lemma.get("overrides")),
        tables_plugin=tables.get("plugin", "legal-citations"),
        tables_jurisdictions=_opt_path(base, tables.get("jurisdictions")),
        raw=data,
    )
