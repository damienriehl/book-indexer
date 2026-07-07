"""Property-test infrastructure — thin re-export shim (Plan 02-04).

The real fixture/constant definitions were hoisted to ``tests/conftest.py``
in Plan 02-04 Task 4.4 so both ``tests/property/`` and
``tests/invariants/`` can consume them without a ``pytest_plugins``
indirection.

What lives here now:
  - Module-level re-exports of ``LEGAL_PHRASE_VOCAB`` and
    ``KNOWN_ABSENT_TERMS`` so existing
    ``from tests.property.conftest import ...`` imports (if any test
    happens to reach into this module directly) still resolve. Pytest
    fixtures themselves are discovered automatically via the parent
    ``tests/conftest.py`` and need not be re-exposed here.

Phase 1's autouse ``frozen_env`` fixture is still inherited from
``tests/conftest.py``; do NOT redeclare PYTHONHASHSEED/TZ/LC_ALL here.
"""
from __future__ import annotations

from tests.conftest import KNOWN_ABSENT_TERMS, LEGAL_PHRASE_VOCAB

__all__ = ["LEGAL_PHRASE_VOCAB", "KNOWN_ABSENT_TERMS"]
