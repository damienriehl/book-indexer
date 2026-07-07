# book-indexer

**A deterministic back-of-book indexer for PDFs — the LLM never assigns a page number; every citation is symbolically verified against the actual page text.**

book-indexer is licensed **AGPL-3.0-only**, because its default PDF-extraction backend is **PyMuPDF**, which is itself distributed under the **AGPL-3.0**; since book-indexer links PyMuPDF as a required dependency, the combined work inherits AGPL-3.0 copyleft obligations, and so the package is released under AGPL-3.0-only to keep the license claim honest and unambiguous. (Roadmap: a future release plans a `PdfBackend` protocol with an MIT-licensed default — pdfplumber — that would let the core be re-licensed under MIT with PyMuPDF as an opt-in `[pymupdf]` extra.)

## Table of contents

- [Purpose](#purpose)
- [Who it's for](#who-its-for)
- [Use cases](#use-cases)
- [Feature highlights](#feature-highlights)
- [Install](#install)
- [Five-minute tutorial: index your first book](#five-minute-tutorial-index-your-first-book)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [License](#license)

## Purpose

Back-of-book indexing is precise, tedious, high-stakes work: a single wrong page
number in a printed treatise is a defect that ships to every reader. book-indexer
automates the *discovery* of index-worthy concepts while refusing to let any
statistical model guess a page number.

The whole package is organized around one guarantee:

> `verify(term, folio, conn) -> Evidence | None` is the **sole** code path that
> emits a page citation.

Concept *discovery* may use NLP — and, optionally, an LLM — to decide *what*
belongs in the index. But the *locator* for every `(term, page)` pair is always
produced by `verify()`, which searches the actual extracted PDF text and returns
`Evidence` only when the term genuinely appears on that page (otherwise `None`).
Because the citation path is fully symbolic and deterministic, the same input PDF
always produces a **byte-identical** index, table of authorities, and audit trail
across runs.

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

- **Index a legal treatise.** Point book-indexer at a chapter-and-section PDF and
  get a Markdown + DOCX subject index with printed-folio page references, plus a
  table of authorities for the cases and statutes it cites.
- **Build a table of authorities for a brief or opinion.** Run the citation-tables
  stage over a court opinion to extract and canonicalize case, statute, and rule
  citations using eyecite + reporters-db + courts-db.
- **Regenerate an index reproducibly in CI.** Because output is byte-identical
  across runs, you can commit the generated index and fail a build if a source
  edit silently changes it.
- **Audit an existing index.** Feed a manuscript through the pipeline and compare
  the machine-verified locators against a hand-built index to catch stale or
  wrong page numbers.
- **Prototype a domain-specific indexer.** Adapt `book.toml` (heading regex,
  lemma overrides, citation-tables plugin) to a new jurisdiction or citation
  style without touching package source.

## Feature highlights

- **Verified locators only.** `verify()` is the sole page-number emitter; a page
  cite exists only if the term is symbolically confirmed on that page.
- **Byte-identical, reproducible output.** Same input PDF ⇒ same index, tables,
  and audit artifacts, every run — diffable in version control.
- **Printed-folio citations.** The public citation is the *printed* folio
  (e.g. page 748), resolved from the page text — never the raw PDF page ordinal.
- **Multi-pass concept discovery.** spaCy noun-phrase extraction, doctrinal-term
  and named-entity passes, with optional LLM-assisted discovery — none of which
  can assign a page number.
- **Legal tables of authorities.** Bundled `legal-citations` plugin extracts and
  canonicalizes cases, statutes, and rules via eyecite + reporters-db + courts-db.
- **Professional output formats.** Emits Markdown and DOCX indexes plus audit
  artifacts for review.
- **Pluggable citation tables.** Plugins register under the `book_indexer.tables`
  entry-point group so new domains can supply their own table builders.
- **Config-driven domains.** `book.toml` captures heading regexes, ground-truth
  fixtures, lemma overrides, and plugin selection per corpus.

## Install

```bash
uv add book-indexer
# or:
pip install book-indexer
```

book-indexer requires the spaCy large English model for its NLP passes. After
installing the package, download the model once:

```bash
python -m spacy download en_core_web_lg
```

## Five-minute tutorial: index your first book

This walkthrough indexes the bundled synthetic treatise,
`samples/synthetic_treatise.pdf` (a 10-page public-domain sample). Make sure you
have installed book-indexer and the `en_core_web_lg` model (see [Install](#install)).

### 1. Run the full pipeline from the CLI

The installed `book-index` script runs every deterministic stage end to end:

```bash
book-index run samples/synthetic_treatise.pdf --out build/
```

When it finishes, the Markdown index lands at `build/index.md`, the DOCX index at
`build/index.docx`, the tables of authorities alongside them, and the audit
artifacts under `build/audit/`. Run the same command twice and diff the outputs —
they are byte-identical.

### 2. Or run the stages individually

Each pipeline stage is also runnable as a module, which is handy for inspecting
intermediate artifacts:

```bash
python -m book_indexer.ingest   samples/synthetic_treatise.pdf --out build/
python -m book_indexer.concepts build/
python -m book_indexer.tables   build/
python -m book_indexer.assembly build/
python -m book_indexer.render   build/
```

### 3. Use the public Python API

The same functionality is available programmatically. `import book_indexer` stays
instant — heavy backends (PyMuPDF, spaCy) load lazily on first use:

```python
import book_indexer

# Load domain configuration and ingest the PDF (text/char extraction,
# printed-folio resolution, section-tree detection).
config = book_indexer.load_book_config("book.toml")
ingest = book_indexer.run_ingest("samples/synthetic_treatise.pdf", config=config)

# verify() is the ONLY function that produces a page citation. It confirms the
# term actually appears on the given printed folio, returning Evidence or None.
evidence = book_indexer.verify("negligence", folio="3", conn=ingest.conn)
if evidence is not None:
    print("confirmed on printed folio", evidence.folio)
else:
    print("term not present on that folio — no citation emitted")

# Assemble the verified concepts into an index tree and render Markdown.
tree = book_indexer.build_index_tree(ingest)
markdown = book_indexer.render_markdown(tree)
```

## How it works

book-indexer is a deterministic pipeline of five stages:

1. **Ingest** — PyMuPDF extracts text and character geometry; the printed folio
   for each page is resolved from the page text (not the PDF ordinal), and the
   section tree (`§`/Chapter headings) is detected.
2. **Concept discovery** — spaCy noun-phrase, doctrinal-term, and named-entity
   passes propose index-worthy concepts. Optional LLM discovery can propose more.
   No pass in this stage may assign a page number.
3. **Citation tables** — the selected tables plugin (bundled `legal-citations`,
   built on eyecite + reporters-db + courts-db) extracts and canonicalizes cases,
   statutes, and rules.
4. **Canonicalization & assembly** — concepts are lemmatized/merged and organized
   into the index tree; each concept is either placed in the index or explicitly
   classified out.
5. **Rendering** — the index and tables of authorities are emitted as Markdown and
   DOCX, alongside audit artifacts.

The invariant that ties it together: **`verify()` is the sole code path that emits
a page citation.** Discovery decides *what* to index; `verify()` decides *where*
it may be cited, by symbolically confirming the term against the extracted page
text. This is why every output is byte-identical across runs. These properties are
enforced by CI invariant tests — including that the LLM's JSON schema has zero
page-like fields, that `src/` imports no LLM SDK, and that the printed folio (not
the PDF ordinal) is the public citation.

## Configuration

Two files separate *package* configuration from *domain* configuration:

- **`pyproject.toml`** — package configuration (dependencies, entry points,
  tooling). You don't normally edit this to index a book.
- **`book.toml`** — domain/corpus configuration for the specific volume you are
  indexing. It holds the source PDF path, the section-heading regex, optional
  hand-verified section/folio ground-truth fixtures, lemma overrides for the
  canonicalizer, and the citation-tables plugin to load. Copy and edit it to
  adapt the indexer to a new treatise, jurisdiction, or citation style without
  touching package source.

Citation-tables plugins register under the **`book_indexer.tables`** entry-point
group. The bundled `legal-citations` plugin ships in v0.1; select it (or a custom
plugin) via the `[tables] plugin = "..."` key in `book.toml`. Additional
non-legal-domain plugins are on the roadmap.

## License

book-indexer is licensed **AGPL-3.0-only**. As explained near the top of this
document, this is a direct consequence of linking PyMuPDF (itself AGPL-3.0) as
the default PDF backend. Any distribution of this package — or any network
service built on it — is governed by the AGPL-3.0.

See [LICENSE](LICENSE) for the full license text and [NOTICE](NOTICE) for the
third-party component inventory and the rationale behind the license choice.
