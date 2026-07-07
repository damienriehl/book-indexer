# Contributing to book-indexer

Thanks for your interest in improving book-indexer. This guide covers local
development setup, testing and linting expectations, how to file issues and open
pull requests, and the licensing and determinism rules that every contribution
must respect.

## Development setup

book-indexer uses [uv](https://docs.astral.sh/uv/) for environment and dependency
management, and targets Python 3.12–3.13.

```bash
# 1. Clone your fork
git clone https://github.com/OWNER/book-indexer.git
cd book-indexer

# 2. Install the package plus the dev extra (pytest, ruff, pyright, etc.)
uv sync --extra dev

# 3. Download the spaCy model the NLP passes require
uv run python -m spacy download en_core_web_lg
```

## Running tests, linting, and type checks

Run the full suite before opening a PR:

```bash
uv run pytest
```

Useful subsets (see markers in `pyproject.toml`):

```bash
uv run pytest -m invariants   # CI ship-blocker architecture invariants
uv run pytest -m integration  # end-to-end ingest over a sample PDF
uv run pytest -m property     # Hypothesis property-based tests
```

Lint and type-check with the same tools CI runs:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Please make sure `pytest`, `ruff`, and `pyright` are all clean before requesting
review.

## Filing issues

Use the GitHub issue templates in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE):

- **Bug report** — include the input (or a minimal synthetic sample), the command
  you ran, what you expected, and what happened. Because output is meant to be
  byte-identical, a diff of expected vs. actual output is especially valuable.
- **Feature request** — describe the use case and the domain you're indexing.

## Opening pull requests

1. Branch from `main` and keep each PR focused.
2. Add or update tests. Any change to a pipeline stage must keep the determinism
   and invariant tests green.
3. Fill out the [pull request template](.github/PULL_REQUEST_TEMPLATE.md),
   including its checklist.
4. Ensure `uv run pytest`, `uv run ruff check .`, and `uv run pyright` all pass.

## Determinism and the architecture invariants

book-indexer's value depends on a small set of invariants that CI enforces. Any
change to the pipeline must preserve all of them:

1. **`verify()` is the sole page-number emitter.** No other code path may produce
   a page citation. Discovery decides *what* to index; only `verify()` decides
   *where* it may be cited.
2. **The LLM JSON schema has zero page-like fields.** Concept discovery may not
   even represent a page number.
3. **No `anthropic` / `claude_agent_sdk` imports in `src/`.**
4. **The printed folio — not the PDF page ordinal — is the public citation.**
5. **Every output is byte-identical across runs** for the same input.
6. **Every discovered concept either reaches the index or is explicitly
   classified** out.

If your change affects any pipeline stage, **verify that output remains
byte-identical** (run the pipeline twice and diff, or run `uv run pytest -m
invariants`). If you intend to change output deliberately, update the relevant
golden fixtures in the same PR and explain the change.

## AGPL-3.0 re-statement

book-indexer is licensed **AGPL-3.0-only** (a consequence of linking the
AGPL-3.0-licensed PyMuPDF backend; see [NOTICE](NOTICE)). **By contributing, you
agree that your contributions are licensed under AGPL-3.0-only**, the same license
as the project. Do not submit code that you cannot license this way, and do not
introduce dependencies whose licenses are incompatible with AGPL-3.0.
