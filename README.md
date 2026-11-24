# book-indexer

**A deterministic back-of-book indexer for PDFs — the LLM never assigns a page number; every citation is symbolically verified against the actual page text.**

book-indexer is licensed **AGPL-3.0-only**, because its default PDF-extraction backend is **PyMuPDF**, which is itself distributed under the **AGPL-3.0**; since book-indexer links PyMuPDF as a required dependency, the combined work inherits AGPL-3.0 copyleft obligations, and so the package is released under AGPL-3.0-only to keep the license claim honest and unambiguous. (Roadmap: a future release plans a `PdfBackend` protocol with an MIT-licensed default — pdfplumber — that would let the core be re-licensed under MIT with PyMuPDF as an opt-in `[pymupdf]` extra.)

## Table of contents

- [Purpose](#purpose)
- [Who it's for](#who-its-for)
- [Use cases](#use-cases)
- [Feature highlights](#feature-highlights)
- [Install](#install)
- [Try the sample PDF](#try-the-sample-pdf)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [License](#license)

## Purpose

Back-of-book indexing is precise, tedious, high-stakes work: a single wrong page
number in a printed treatise is a defect that ships to every reader. book-indexer
automates the *discovery* of index-worthy concepts while refusing to let any
statistical model guess a page number.

> `verify()` is the sole source of page locators. It yields `Evidence` only for
> occurrences it confirms in the extracted page text and links to a section.

Concept discovery can use NLP or an optional LLM to decide what belongs in the
index. The verifier searches the corpus for each term, across all pages. Each
match includes the printed folio, PDF page number, section reference, and a
verbatim snippet. If the verifier finds no matches, it yields nothing.

## Who it's for

- **Legal publishers and treatise authors** who need a professional back-of-book
  subject index plus tables of authorities (cases, statutes, rules) for a
  paginated volume, and cannot tolerate a fabricated page cite.
- **Law librarians** producing finding aids and pinpoint indexes over court
  opinions, reporters, and agency materials where the printed folio is the
  citation of record.
- **Professional indexers** who want to accelerate concept discovery while
  keeping final locator authority in a verifiable, reviewable machine step
  rather than in a black-box model.
- **Document-processing engineers** building pipelines that ingest paginated
  PDFs and emit structured, reproducible artifacts, who need a component whose
  output is stable enough to diff in CI.
- **Researchers and standards bodies** who need auditable, reproducible indexes
  over public-domain legal corpora.

## Use cases

- **Index a legal treatise.** For a PDF with supported headings, the pipeline
  can build a subject index with printed-folio references and tables of
  authorities in Markdown and DOCX.
- **Build a table of authorities for a brief or opinion.** Run the citation-tables
  stage over a court opinion to extract and canonicalize case, statute, and rule
  citations using eyecite + reporters-db + courts-db.
- **Check a completed build.** Replay compares regenerated artifacts with the
  saved build and returns an error if they differ.
- **Audit an existing index.** Feed a manuscript through the pipeline and compare
  the machine-verified locators against a hand-built index to catch stale or
  wrong page numbers.

## Feature highlights

- **Verified locators only.** The verifier confirms each locator against the
  page text.
- **Reproducible output.** Replay compares the regenerated index, tables, and
  audit artifacts byte for byte with the previous build.
- **Printed-folio citations.** The public citation is the *printed* folio
  (e.g. page 748), resolved from the page text — never the raw PDF page ordinal.
- **Multi-pass concept discovery.** spaCy noun-phrase extraction, doctrinal-term
  and named-entity passes, with optional LLM-assisted discovery — none of which
  can assign a page number.
- **Legal tables of authorities.** The bundled `legal-citations` resolver
  extracts and canonicalizes cases, statutes, and rules.
- **Professional output formats.** Emits Markdown and DOCX indexes plus audit
  artifacts for review.

## Install

From the repository root, use [uv](https://docs.astral.sh/uv/) to install the
package and download the spaCy model:

```bash
uv sync --extra dev
uv run --extra dev python -m spacy download en_core_web_lg
```

## Try the sample PDF

Run these examples from the repository root after completing [Install](#install).
They use `samples/synthetic_treatise.pdf`, a 10-page synthetic sample. Ingest
extracts its text and printed folios. The sample lacks the heading fonts the
section detector expects, so concept discovery produces no candidates and a
full build stops at assembly.

### 1. Inspect the CLI

```bash
uv run --extra dev book-index --help
```

The `build` command takes a PDF path. With `--rebuild-all`, it passes that
path to ingest and runs the downstream stages. Tables and assembly still
default to the bundled sample. Plain `build` renders from existing artifacts.
A successful build writes the subject index and tables of authorities to
`artifacts/render/index.md` and `artifacts/render/index.docx`. The
sections-only versions (`index_sections_only.md` and `index_sections_only.docx`)
share that directory. Find the audit files in `artifacts/audit/`.

`book-index replay` needs artifacts from all four downstream stages. It
regenerates their outputs for a byte-for-byte comparison. Its tables and
assembly replay steps also use the bundled sample PDF.

### 2. Run sample stages

Run ingest, concept discovery, and citation tables in order. The stages share
`artifacts/`. Set the hash seed, time zone, and locale before running them.

```bash
export PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8
uv run --extra dev python -m book_indexer.ingest samples/synthetic_treatise.pdf
uv run --extra dev python -m book_indexer.concepts build
uv run --extra dev python -m book_indexer.tables build
```

For a corpus with concept candidates, the `book_indexer.assembly` module's
`build` subcommand creates the index tree. The `book_indexer.render` module's
`build` subcommand then renders it.

### 3. Use the Python API

Run the snippet with `uv run --extra dev python -`. It reads the sample PDF path
from `book.toml` and builds a SQLite corpus. The word “procedure” appears in the
sample. The verifier reports zero occurrences because the sample has no detected
sections.

```python
import sqlite3
from contextlib import closing

from book_indexer import load_book_config, run_ingest
from book_indexer.verify import verify

config = load_book_config("book.toml")
corpus_path = run_ingest(config.source_pdf, "artifacts/api-example")
with closing(sqlite3.connect(corpus_path.as_uri() + "?mode=ro", uri=True)) as conn:
    matches = list(verify("procedure", conn))

for evidence in matches:
    print(evidence.folio, evidence.section_ref, evidence.verbatim_snippet)
print(f"Verified occurrences: {len(matches)}")
```

See [assembly's entry point](src/book_indexer/assembly/__main__.py) and
[rendering's entry point](src/book_indexer/render/__main__.py) for the full
assembly and rendering calls.

## How it works

book-indexer is a deterministic pipeline of five stages:

1. **Ingest** — PyMuPDF extracts text and character geometry; the printed folio
   for each page is resolved from the page text (not the PDF ordinal), and the
   section tree (`§`/Chapter headings) is detected.
2. **Concept discovery** — spaCy noun-phrase, doctrinal-term, and named-entity
   passes propose index-worthy concepts. Optional LLM discovery can propose more.
   No pass in this stage may assign a page number.
3. **Citation tables** — the bundled `legal-citations` resolver extracts and
   canonicalizes cases, statutes, and rules using eyecite, reporters-db, and courts-db.
4. **Canonicalization & assembly** — concepts are lemmatized/merged and organized
   into the index tree; each concept is either placed in the index or explicitly
   classified out.
5. **Rendering** — the index and tables of authorities are emitted as Markdown and
   DOCX, alongside audit artifacts.

Call `verify(term, conn, *, variants_for=None) -> Iterator[Evidence]` to look up
a term in the SQLite corpus. The iterator yields one record per verified
occurrence in page and token order. Invariant tests enforce the single locator
source, the absence of page-like fields in the LLM schema, the absence of LLM SDK
imports in `src/`, and the use of printed folios for public citations.

## Configuration

Two files separate package configuration from domain configuration.

- **`pyproject.toml`** — package configuration (dependencies, entry points,
  tooling). You don't normally edit this to index a book.
- **`book.toml`** — domain configuration for a volume. It stores the source PDF
  path, the section-heading regex, optional section and folio fixtures, and
  lemma overrides.

Plugin selection is not wired yet. The config loader parses the `plugin` key
under `[tables]`, and the package declares the `book_indexer.tables` entry-point
group. Both are reserved for future plugin loading. In v0.1 the tables stage
always runs the bundled `legal-citations` resolver.

## License

book-indexer is licensed **AGPL-3.0-only**. As explained near the top of this
document, this is a direct consequence of linking PyMuPDF (itself AGPL-3.0) as
the default PDF backend. Any distribution of this package — or any network
service built on it — is governed by the AGPL-3.0.

See [LICENSE](LICENSE) for the full license text, [NOTICE](NOTICE) for the
rationale behind the license choice, and [THIRD-PARTY.md](THIRD-PARTY.md) for the
per-component inventory — every dependency, its license, and what it does.
