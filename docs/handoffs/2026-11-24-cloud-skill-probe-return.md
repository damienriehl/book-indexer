# Return handoff: README review probe

Rules stamp — `portable-rules 2026-11-18 ff56f96ad849`

## State

- **Done:** 2026-11-24, the cloud lane reviewed `README.md` against `src/` and
  wrote `docs/reviews/2026-11-24-readme-review.md` with five fixes.
- **Branch:** `claude/cloud-skill-probe`; pull request open against `main`,
  unmerged.
- **Code:** unchanged. Only `docs/` gained files.

## Constraints

- The four highest-ranked fixes concern commands and code a reader copies; each
  fails today. Verify any rewritten example by running it, since this review
  read signatures and did not execute the README snippets.
- Two stale strings live in `src/` (the `verify()` docstring in
  `book_indexer/__init__.py` and the `index-book` name in `cli/`). A README-only
  fix leaves them inconsistent.

## Next step

The home lane picks which fixes to apply, edits `README.md`, and decides
whether fix 5 means documenting the gap or wiring plugin loading.
