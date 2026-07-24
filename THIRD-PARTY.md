# Third-Party Components & Attribution

book-indexer is licensed **AGPL-3.0-only** (see [LICENSE](LICENSE)). The
*why* — PyMuPDF's AGPL flowing into the combined work — lives in [NOTICE](NOTICE)
and at the top of the [README](README.md). This file is the per-component
inventory: every dependency in `pyproject.toml`, its license, and what it does.

Update it whenever a dependency is added, removed, or changed.

## The license-determining dependency

| Component | License | Why it matters |
|---|---|---|
| [PyMuPDF](https://pymupdf.readthedocs.io/) (`pymupdf`) | **GNU AGPL-3.0** (or a commercial license from Artifex) | The default PDF text/character-extraction backend, linked as a **required** dependency. Its copyleft is what makes book-indexer AGPL-3.0-only. Roadmap PKG item: a `PdfBackend` protocol with an MIT-licensed default (pdfplumber) would move PyMuPDF to an opt-in `[pymupdf]` extra and free the core to be re-licensed MIT. |

Until that abstraction ships, assume AGPL-3.0 governs any distribution of this
package **and any network service built on it** (AGPL §13).

## Runtime dependencies

| Component | License | Used for |
|---|---|---|
| [spaCy](https://spacy.io/) | MIT | Tokenization, noun-phrase / named-entity concept passes |
| `en_core_web_lg` model | MIT (Explosion) | The English pipeline spaCy loads; downloaded at install time, not vendored |
| [pydantic](https://github.com/pydantic/pydantic) | MIT | Schema validation for every artifact |
| [typer](https://github.com/fastapi/typer) | MIT | The `book-index` CLI |
| [click](https://github.com/pallets/click) | BSD-3-Clause | Declared explicitly because `import spacy` needs it and typer ≥0.16 dropped the transitive |
| [orjson](https://github.com/ijl/orjson) | Apache-2.0 OR MIT | Deterministic JSON for the audit artifacts |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT | Config loading |
| [inflect](https://github.com/jaraco/inflect) | MIT | Singular/plural handling in canonicalization |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | MIT | Markdown parsing/rendering |
| [python-docx](https://github.com/python-openxml/python-docx) | MIT | DOCX index + table emit |
| [pandas](https://pandas.pydata.org/) | BSD-3-Clause | Audit artifact tabulation |
| [matplotlib](https://matplotlib.org/) | Matplotlib License (BSD-style, PSF-derived) | Audit charts |
| [eyecite](https://github.com/freelawproject/eyecite) | BSD-2-Clause | Legal citation extraction (bundled `legal-citations` plugin) |
| [reporters-db](https://github.com/freelawproject/reporters-db) | BSD-2-Clause | Reporter metadata for citation canonicalization |
| [courts-db](https://github.com/freelawproject/courts-db) | BSD-2-Clause | Court metadata for citation canonicalization |

Every component above is permissive and compatible with AGPL-3.0 distribution.
PyMuPDF is the only copyleft dependency.

## Dev dependencies (`[dev]` extra — not distributed)

| Component | License |
|---|---|
| pytest, pytest-cov | MIT |
| hypothesis | MPL-2.0 (file-level copyleft; test-only) |
| ruff, pyright, pre-commit | MIT |
| hatchling (build backend) | MIT |

## Sample documents

Files under `samples/` are public-domain U.S. federal government edicts plus
synthetic content authored for this repo. Per-file provenance is recorded in
[`samples/PROVENANCE.md`](samples/PROVENANCE.md).
