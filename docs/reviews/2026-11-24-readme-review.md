# README review — 2026-11-24

Scope: `README.md` checked against `src/book_indexer/` at commit `acbf5d4`.
No code changed. Fixes rank by reader impact: a reader who copies the README's
commands or code today hits an error on each of the first four.

## 1. CLI: `book-index run` does not exist

- **README says:** `book-index run samples/synthetic_treatise.pdf --out build/`,
  with outputs under `build/`.
- **Code says:** `cli/app.py` registers `build`, `replay`, `verify-against`, and
  `review`. `build` takes no `--out`. Rendered files land in `artifacts/render/`
  (`index.md`, `index.docx`, plus `index_sections_only.*`) and audit files in
  `artifacts/audit/` (`render/__main__.py`, `_RENDER_OUTPUTS`). Plain `build`
  only re-renders from committed artifacts; a fresh run from the PDF needs
  `--rebuild-all` (`cli/subcommands/build.py`).
- **Fix:** replace the tutorial step with
  `book-index build samples/synthetic_treatise.pdf --rebuild-all`, name the
  `artifacts/render/` and `artifacts/audit/` paths, and show `book-index replay`
  as the byte-identity check instead of "run twice and diff."

## 2. Stage commands use the wrong arguments

- **README says:** `python -m book_indexer.<stage> build/` for each stage.
- **Code says:** ingest takes `PDF [--out-dir DIR]` (default `artifacts/`,
  `cli/ingest_main.py`). `concepts`, `tables`, `assembly`, and `render` take a
  `build` or `replay` subcommand and read and write fixed paths under
  `artifacts/`; none accepts a directory positional.
- **Fix:** show `python -m book_indexer.ingest samples/synthetic_treatise.pdf`
  followed by `python -m book_indexer.<stage> build` for the other four, and
  state that stages share `artifacts/`.

## 3. `verify()` signature is stale in three places

- **README says:** `verify(term, folio, conn) -> Evidence | None`, in the
  Purpose quote, How it works, and the API example (`folio="3"`).
- **Code says:** `verify(term, conn, *, variants_for=None) -> Iterator[Evidence]`
  (`verify/verifier.py:35`). It takes no folio; it yields every verified
  occurrence, each `Evidence` carrying `folio`, `pdf_page`, `section_ref`, and a
  verbatim snippet.
- **Fix:** restate the guarantee as "`verify()` is the sole source of page
  locators; it yields `Evidence` only for occurrences it confirms in the page
  text." Update the example to iterate over `verify("negligence", conn)`.
  Note for home lane: the `book_indexer/__init__.py` docstring repeats the stale
  signature; fixing it falls outside this docs-only run.

## 4. Python API example fails at every call

- **README says:** `run_ingest(pdf, config=config)` returns an object with
  `.conn`; `build_index_tree(ingest)`; `render_markdown(tree)`.
- **Code says:**
  - `run_ingest(pdf_path, out_dir) -> Path` returns the SQLite path; it takes no
    `config` (`ingest/pipeline.py:149`).
  - `build_index_tree(concepts_dir, corpus_path, nlp, pdf_sha256, ...)`
    (`assembly/tree.py:457`).
  - `render_markdown(tree, synthetics, tables, metadata)`
    (`render/markdown.py:631`).
- **Fix:** cut the example to the calls a reader can run unaided:
  `load_book_config`, `run_ingest(pdf, "artifacts")`, `sqlite3.connect` on the
  returned path, and the `verify()` loop. Point to `render/__main__.py` for the
  full assembly and render wiring rather than show partial signatures.

## 5. Plugin selection overstates what ships

- **README says:** select a tables plugin with `[tables] plugin = "..."` in
  `book.toml`; plugins register under `book_indexer.tables`.
- **Code says:** `config.py` parses the key into `BookConfig.tables_plugin`, and
  `pyproject.toml` declares the entry point, but nothing in `src/` reads
  `tables_plugin` or calls `entry_points()`. The tables stage always runs the
  bundled legal resolver.
- **Fix:** say the key and entry-point group are reserved for v0.2 plugin
  loading and that v0.1 always runs `legal-citations`. Drop "Pluggable citation
  tables" from Feature highlights until loading lands.

## Clarity notes (no fix slot used)

- **License lead:** the opening license paragraph runs one 49-word sentence,
  plus a roadmap aside, before the reader learns what the tool does. Move it below Purpose, cut it to
  two sentences, and link `NOTICE`.
- **Script name:** docstrings in `cli/` call the script `index-book`;
  `pyproject.toml` installs `book-index`. The README uses `book-index`, which
  matches the install. The docstrings and Typer `name=` need the home lane.
