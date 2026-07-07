"""Invariants-suite conftest.

Re-exports the ``fabricated_bad_responses`` fixture from
``tests/unit/concepts/conftest.py`` so CON-07's ship-blocker
(``tests/invariants/test_concept_schema_rejects_locators.py``) can consume
the single source of truth for the D-19 six-shape matrix.

Pytest's conftest-resolution walks **upward** from a test's location; it
does NOT descend into sibling directories. Without this bridge, the
invariants test would need to declare its own fixture, which would create
two sources of truth for the D-19 matrix and invite drift. The re-export
is read-only — any change to the payload shapes still happens in the
unit-scope conftest (Plan 03A-01's intended owner).

Rule 3 (blocking): without this file, pytest fails with
``fixture 'fabricated_bad_responses' not found`` at setup time of every
parametrized case in the ship-blocker test.
"""
from __future__ import annotations

from tests.unit.concepts.conftest import fabricated_bad_responses  # noqa: F401
