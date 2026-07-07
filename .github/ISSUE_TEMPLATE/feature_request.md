---
name: Feature request
about: Suggest an enhancement or a new capability
title: "[Feature] "
labels: enhancement
assignees: ''
---

## Problem / motivation

Describe the problem you're trying to solve or the use case that isn't currently
supported. What document, domain, or jurisdiction are you indexing?

## Proposed solution

A clear and concise description of what you'd like to happen.

## Alternatives considered

Any alternative solutions or workarounds you've considered.

## Invariant impact

book-indexer is built around a set of architecture invariants. Please note
whether this request interacts with any of them:

- [ ] Keeps `verify()` as the sole page-number emitter
- [ ] Does not introduce page-like fields into the LLM schema
- [ ] Does not add an LLM SDK import to `src/`
- [ ] Preserves byte-identical, deterministic output
- [ ] N/A — this request doesn't touch the pipeline invariants

## Additional context

Add any other context, mockups, or examples here.
