# book-indexer

book-indexer builds deterministic back-of-book indexes and tables of authorities from PDFs.

## Tests

Run the full suite from the repository root.

```sh
uv run --extra dev pytest -q
```

uv installs the locked Python dependencies and the dev extra. The suite also
requires the spaCy model `en_core_web_lg`, which the lockfile omits. See
[Development setup](CONTRIBUTING.md#development-setup) for the model setup.

## Layout

- `src/book_indexer/` holds the `book-index` CLI and the indexing pipeline.
- `tests/` holds unit, integration, property, and architecture invariant tests.
- `config/` holds legal lemma overrides. The root `book.toml` configures the corpus.
- `fixtures/` holds YAML vocabularies, indexing rules, and golden expectations.
- `samples/` holds sample PDFs, their provenance, and the synthetic PDF generator.
- `.github/` holds issue templates, a pull request template, and workflows.

## Conventions

Follow the [architecture invariants](CONTRIBUTING.md#determinism-and-the-architecture-invariants)
when changing pipeline stages. See the [contribution guide](CONTRIBUTING.md)
for checks and contribution requirements. Ruff and Pyright use `pyproject.toml`.
