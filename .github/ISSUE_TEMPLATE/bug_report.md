---
name: Bug report
about: Report incorrect behavior, a wrong locator, or non-deterministic output
title: "[Bug] "
labels: bug
assignees: ''
---

## Description

A clear and concise description of what the bug is.

## To reproduce

Steps to reproduce the behavior:

1. Input document (attach a minimal synthetic sample if possible):
2. Command run (e.g. `book-index run ... --out build/`):
3. `book.toml` settings relevant to the issue:

## Expected behavior

What you expected to happen.

## Actual behavior

What actually happened. If output changed unexpectedly or differs between runs,
please paste a diff of expected vs. actual output — book-indexer's output is
meant to be byte-identical across runs.

## Locator / determinism impact

- [ ] This produced an incorrect or missing page citation (`verify()` result)
- [ ] This produced non-deterministic / non-byte-identical output
- [ ] Neither of the above

## Environment

- book-indexer version:
- Python version:
- Operating system:
- `en_core_web_lg` installed (`python -m spacy validate`): yes / no

## Additional context

Add any other context, logs, or audit artifacts here.
