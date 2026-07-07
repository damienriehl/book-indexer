# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-07

Initial public release, extracted from an internal project into a standalone
open-source package.

### Added

- **Deterministic indexing pipeline** with five stages — ingest (PyMuPDF
  text/character extraction, printed-folio resolution, section-tree detection),
  concept discovery (spaCy noun-phrase, doctrinal, and NER passes, plus optional
  LLM discovery), citation tables, canonicalization & index assembly, and
  rendering to Markdown and DOCX with audit artifacts.
- **Symbolic page-citation verification.** `verify(term, folio, conn)` is the
  sole code path that emits a page citation; the LLM never assigns a page number.
  Output is byte-identical across runs for the same input.
- **Architecture invariants enforced by CI tests**, including that `verify()` is
  the sole locator source, the LLM JSON schema has zero page-like fields, `src/`
  imports no LLM SDK, and the printed folio (not the PDF ordinal) is the public
  citation.
- **`book-index` CLI** entry point, plus per-stage module execution
  (`python -m book_indexer.<stage>`).
- **Bundled `legal-citations` tables plugin** (eyecite + reporters-db +
  courts-db), registered under the `book_indexer.tables` entry-point group.
- **`book.toml` domain configuration** for heading regexes, section/folio
  ground-truth fixtures, lemma overrides, and plugin selection.
- **Public-domain samples** under `samples/` — a synthetic 10-page treatise and a
  real public-domain U.S. Supreme Court opinion (see `samples/PROVENANCE.md`).

### Licensing

- Released under **AGPL-3.0-only**, inherited from the AGPL-3.0-licensed PyMuPDF
  PDF backend (see `NOTICE`).

[Unreleased]: https://github.com/OWNER/book-indexer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/OWNER/book-indexer/releases/tag/v0.1.0
