"""Phase 4 assembly: dedup → canonical → sweep → cite-rule → subdivide → cross-refs → tree.

Public API: the Pydantic IR. ``Locator`` is re-exported from
``book_indexer.tables.ir`` (single source of truth per D-07 — NOT redefined).

requirements_addressed: ASM-01 (canonical-form selection), ASM-08 (frozen
Pydantic IR for the IndexTree).
"""
from book_indexer.tables.ir import Locator

from .cite_rule import cite_for_canonical, group_by_chapter, lowest_common_ancestor
from .coverage import ASM07_MAX, ASM07_MIN, compute_oob_status, emit_draft_report
from .cross_refs import (
    build_see_also_edges,
    build_see_edges,
    check_out_degree,
    find_cycle,
    find_dangling,
    validate_graph,
)
from .errors import (
    AssemblyError,
    CycleDetectedError,
    DanglingRefError,
    EmptyConceptsError,
    OversizeAfterIterationError,
)
from .ir import IndexEntry, IndexTree, IndexTreeProvenance, SubEntry
from .subdivide import compute_co_occurrence, subdivide_oversize
from .tree import build_index_tree, compute_id, compute_sort_key, slugify
from .verifier_sweep import EvidenceByCanonical, run_sweep, sweep_canonical

__all__ = [
    "ASM07_MAX",
    "ASM07_MIN",
    "AssemblyError",
    "CycleDetectedError",
    "DanglingRefError",
    "EmptyConceptsError",
    "EvidenceByCanonical",
    "IndexEntry",
    "IndexTree",
    "IndexTreeProvenance",
    "Locator",
    "OversizeAfterIterationError",
    "SubEntry",
    "build_index_tree",
    "build_see_also_edges",
    "build_see_edges",
    "check_out_degree",
    "cite_for_canonical",
    "compute_co_occurrence",
    "compute_id",
    "compute_oob_status",
    "compute_sort_key",
    "emit_draft_report",
    "find_cycle",
    "find_dangling",
    "group_by_chapter",
    "lowest_common_ancestor",
    "run_sweep",
    "slugify",
    "subdivide_oversize",
    "sweep_canonical",
    "validate_graph",
]
