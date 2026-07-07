"""Typed exceptions for Phase 4 assembly stages.

Each exception corresponds to a build-failure scenario in
RESEARCH §H-13 (Failure-Mode Handling). Modules under ``assembly/`` raise
these so callers (CLI, tests) can branch on type rather than parse messages.
"""
from __future__ import annotations


class AssemblyError(Exception):
    """Base for all assembly errors."""


class EmptyConceptsError(AssemblyError):
    """Raised by ``dedup.py`` when ``artifacts/concepts/*.json`` is empty.

    Indicates an upstream Phase 3a failure (cold-build cache missed every
    chunk, or the concepts directory was wiped). Wave 1 cannot proceed.
    """


class CycleDetectedError(AssemblyError):
    """Raised by ``cross_refs.py`` when DFS finds a cycle in ``see`` edges.

    Carries the cycle path as a list of IndexEntry.id values
    (e.g., ``['a', 'b', 'a']``) for diagnostic output.
    """


class DanglingRefError(AssemblyError):
    """Raised by ``cross_refs.py`` when a ``see``/``see_also`` target id
    is not in IndexTree.entries[*].id.

    Carries ``(source_id, edge_type, target_id)`` tuples for the failing
    references so the operator can fix them.
    """


class OversizeAfterIterationError(AssemblyError):
    """Raised by ``subdivide.py`` if a parent remains >7 locators
    after iteration depth 2 (the bounded-recursion ceiling per D-04)."""
