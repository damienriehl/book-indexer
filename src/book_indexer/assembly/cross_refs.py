"""See / See also cross-ref builder + graph validation per ASM-05 + ASM-06.

Two construction passes over the canonical pool:

  * :func:`build_see_edges` — variant → canonical (mechanical, no
    threshold). Every Phase 3a / Phase 3b variant surface becomes a
    ``See <canonical>`` pointer. The Locator's slugified variant is the
    edge KEY; its IndexEntry.id is the edge VALUE.

  * :func:`build_see_also_edges` — canonical ↔ canonical (≥3 distinct
    sub-sections, bounded out-degree of 5, symmetric). Per CONTEXT.md
    D-03 + RESEARCH §H-7: high-frequency concepts ("evidence") co-occur
    with everything; the bounded out-degree prevents graph explosion.

Plus three validation checks (RESEARCH §H-9):

  * :func:`find_cycle` — DFS cycle detection on ``see`` edges only
    (directional). See-also is undirected by design — A→B and B→A is the
    SAME edge — so 2-cycles are EXPECTED and NOT flagged.
  * :func:`find_dangling` — every ``see`` and ``see_also`` target id
    must exist in the IndexEntry.id set.
  * :func:`check_out_degree` — defense in depth on ``see_also`` length;
    the build pass should already truncate at 5, but a regression in
    the build path or hand-edited entries would slip through without
    this gate.

Per CONTEXT.md D-03: cross-refs build AFTER zero-evidence drops (this
module is invoked LATE in tree.py). By the time we run, all surviving
entries have non-zero locators — we never need to handle "target was
dropped" within this module.

Architecture Lock #1: this module never constructs ``Evidence``. It
operates on ``IndexEntry`` data only.

requirements_addressed: ASM-05 (See / See also edges), ASM-06 (graph
validation).
"""
from __future__ import annotations

import re
from collections import defaultdict

from .errors import CycleDetectedError, DanglingRefError
from .ir import IndexEntry

# Slug regex: identical implementation to Plan 04-04's tree.py to avoid
# circular imports. Plan 04-04 mirrors this implementation.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(s: str) -> str:
    """Lowercase + collapse non-alphanumeric runs to single ``-``.

    Matches the IndexEntry.id pattern
    ``r"^[a-z0-9][-a-z0-9]*(-\\d+)?$"``. Returns ``"x"`` for empty
    inputs (no production caller should pass empty, but defensive).
    """
    s = s.lower().strip()
    s = _SLUG_RE.sub("-", s)
    s = s.strip("-")
    return s or "x"


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def build_see_edges(
    canonicals: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Map every variant slug to its canonical id (mechanical).

    Args:
        canonicals: ``{canonical_id: [variant_surface, ...]}``. The
            ``canonical_id`` is the IndexEntry.id; variants are surface
            strings (acronym expansions, lemma variants, etc.).

    Returns:
        ``{variant_slug: [canonical_id, ...]}``. Each variant slug
        usually points to exactly one canonical (no threshold). If a
        variant slug collides across two canonicals, both are listed
        (rare edge case — caller decides resolution policy). Variants
        whose slug equals the canonical id are skipped (self-See has
        no rendering value).
    """
    edges: dict[str, list[str]] = defaultdict(list)
    for cid, variants in canonicals.items():
        for v in variants:
            slug = _slugify(v)
            if slug == cid:
                # Self-See: variant equals canonical id, no edge.
                continue
            if cid not in edges[slug]:
                edges[slug].append(cid)
    return {k: sorted(v) for k, v in edges.items()}


def build_see_also_edges(
    canonicals: dict[str, IndexEntry],
    threshold: int = 3,
    max_out_degree: int = 5,
) -> dict[str, list[str]]:
    """Build symmetric See-also edges per D-03.

    Two canonicals connect IFF they co-occur in ``>= threshold`` distinct
    sub-sections (counted at the section_ref level present in each
    entry's locators — typically the deepest level). Bounded out-degree
    of ``max_out_degree`` per canonical; ties broken alphabetically by
    target id.

    Args:
        canonicals: ``{canonical_id: IndexEntry}``.
        threshold: minimum shared sub-sections for an edge (default 3,
            per D-03; do NOT lower).
        max_out_degree: cap on edges per canonical (default 5, per D-03;
            do NOT raise).

    Returns:
        ``{canonical_id: [target_id, ...]}``. Lists are alphabetically
        sorted (matches IndexEntry.see_also sort policy from D-07).
        Canonicals with no surviving edges are absent from the result.
    """
    refs_by_id: dict[str, set[str]] = {
        cid: {loc.section_ref for loc in entry.locators}
        for cid, entry in canonicals.items()
    }

    edges: dict[str, list[tuple[int, str]]] = defaultdict(list)
    ids = sorted(canonicals.keys())
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            co = len(refs_by_id[a] & refs_by_id[b])
            if co >= threshold:
                edges[a].append((co, b))
                edges[b].append((co, a))

    result: dict[str, list[str]] = {}
    for cid, candidates in edges.items():
        # Higher co-count first; alphabetical tiebreak among equal co's.
        candidates.sort(key=lambda x: (-x[0], x[1]))
        top = candidates[:max_out_degree]
        result[cid] = sorted(b for _, b in top)
    return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def find_cycle(
    edges: dict[str, list[str]],
) -> tuple[bool, list[str] | None]:
    """DFS cycle detection on a directed graph.

    Returns:
        ``(True, cycle_path)`` where ``cycle_path[0] == cycle_path[-1]``
        is the back-edge target; or ``(False, None)`` if the graph is
        a DAG.

    Determinism: traversal order is sorted alphabetically over starting
    nodes so a cycle is reported the same way every run.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(lambda: WHITE)
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in edges.get(node, []):
            if color[nxt] == GRAY:
                # Back-edge → cycle. Reconstruct path from nxt to current.
                idx = stack.index(nxt)
                return stack[idx:] + [nxt]
            if color[nxt] == WHITE:
                cycle = dfs(nxt)
                if cycle is not None:
                    return cycle
        color[node] = BLACK
        stack.pop()
        return None

    for node in sorted(edges):
        if color[node] == WHITE:
            cycle = dfs(node)
            if cycle is not None:
                return True, cycle
    return False, None


def find_dangling(entries: list[IndexEntry]) -> list[tuple[str, str, str]]:
    """Return ``[(source_id, edge_type, target_id), ...]`` for unresolved
    cross-ref targets. Empty list = no dangling.

    Both ``see`` and ``see_also`` targets are checked against the set of
    valid IndexEntry.id values. Order is preserved per source entry.
    """
    valid_ids = {e.id for e in entries}
    dangling: list[tuple[str, str, str]] = []
    for entry in entries:
        for target in entry.see:
            if target not in valid_ids:
                dangling.append((entry.id, "see", target))
        for target in entry.see_also:
            if target not in valid_ids:
                dangling.append((entry.id, "see_also", target))
    return dangling


def check_out_degree(
    entries: list[IndexEntry], max_see_also: int = 5
) -> list[tuple[str, int]]:
    """Return ``[(id, count), ...]`` for entries exceeding the
    ``see_also`` out-degree bound. Empty list = all entries within bound.
    """
    return [(e.id, len(e.see_also)) for e in entries if len(e.see_also) > max_see_also]


def validate_graph(entries: list[IndexEntry], max_see_also: int = 5) -> None:
    """Run all three checks. Raises on any violation.

    Checks run in order: cycle → dangling → out-degree. The first
    failure short-circuits — operators fix issues one at a time.

    Raises:
        CycleDetectedError: ``see`` edges contain a directed cycle.
        DanglingRefError: a ``see`` or ``see_also`` target id is not
            in the entries' id set.
        AssertionError: a ``see_also`` list exceeds ``max_see_also``
            (defense-in-depth — build_see_also_edges should already
            truncate at construction time).
    """
    # 1) Acyclic on `see` only (see_also is undirected; 2-cycles
    #    are expected — A→B and B→A is the SAME edge by design).
    see_edges = {e.id: list(e.see) for e in entries}
    has_cycle, cycle = find_cycle(see_edges)
    if has_cycle:
        raise CycleDetectedError(f"Cycle in `see` edges: {cycle}")

    # 2) No dangling on either edge type.
    dangling = find_dangling(entries)
    if dangling:
        raise DanglingRefError(f"Dangling cross-refs: {dangling}")

    # 3) Bounded see_also out-degree (defense in depth).
    over = check_out_degree(entries, max_see_also=max_see_also)
    assert not over, f"Entries with see_also > {max_see_also}: {over}"
