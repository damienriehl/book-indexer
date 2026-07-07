"""Section-tree construction (Plan 01-03-b Task 3b.2, D-25).

Turns the flat list of :class:`Section`s produced by :class:`SectionResolver`
into a forest (multiple chapter roots) with ``parent_id`` edges populated.

Edges:
  - Level 0 (Chapter) → None (root)
  - Level 1 (§ N) → the enclosing Chapter in the same ``chapter`` scope
  - Level 2 (N.NN) → the enclosing § N in the same ``chapter`` scope
  - Level 3 (N.NN.M) → the enclosing N.NN in the same ``chapter`` scope
    (resolved by the ``N.NN`` prefix of the level-3 ref)

``parent_global_id`` was already populated by the resolver (using the
``ch{N}:{ref}`` global_id convention); this module assigns integer
``parent_id`` values suitable for the SQLite FK column by stamping each
:class:`Section` with a fresh 1-based ``section_id``.

A forest invariant is enforced: every non-root node has exactly one parent;
no cycles (which is architecturally impossible since parent levels strictly
precede child levels, but we still verify).
"""
from __future__ import annotations

from dataclasses import replace

from .section_resolver import Section


def build_section_tree(sections: list[Section]) -> list[Section]:
    """Assign ``section_id`` and ``parent_id`` fields by resolving
    ``parent_global_id`` references.

    Args:
        sections: resolver output — ``parent_global_id`` is set but
            ``section_id`` is still ``-1``.

    Returns:
        A new list of :class:`Section` instances with both ``section_id`` and
        ``parent_id`` populated. Order is preserved (``(start_pdf_page,
        start_token_offset)``) so downstream code can rely on it.

    Raises:
        ValueError: if a non-root node's ``parent_global_id`` doesn't resolve
            to any section in the input, or a cycle is detected (defensive —
            the level-ordered input cannot produce a cycle, but we check).
    """
    # Pass 1: stamp integer IDs in input order so downstream code sees a dense
    # 1..N ID space. The SQLite corpus writer (plan 01-04) may reassign; the
    # IDs produced here are stable in-memory references.
    id_by_gid: dict[str, int] = {}
    stamped: list[Section] = []
    for idx, s in enumerate(sections):
        new_id = idx + 1
        id_by_gid[s.global_id] = new_id
        stamped.append(replace(s, section_id=new_id))

    # Pass 2: resolve parent_id using parent_global_id.
    out: list[Section] = []
    for s in stamped:
        if s.parent_global_id is None:
            # Root — must be a level-0 (Chapter). Level-1 sections with no
            # enclosing chapter (pre-§1 front matter) also land here; that is
            # acceptable per CONTEXT §Pre-§-1 front-matter deferred-item.
            out.append(s)
            continue
        pid = id_by_gid.get(s.parent_global_id)
        if pid is None:
            raise ValueError(
                f"Section {s.global_id!r}: parent {s.parent_global_id!r} "
                f"not found in section tree input"
            )
        out.append(replace(s, parent_id=pid))

    # Forest invariant: no cycles. Since parent IDs are always lower than
    # child IDs (children come strictly AFTER parents in reading order), a
    # cycle is impossible — but we verify defensively by walking each chain
    # to a root, capped by the tree size.
    limit = len(out) + 1
    for s in out:
        pid = s.parent_id
        hops = 0
        while pid is not None:
            if hops > limit:
                raise ValueError(
                    f"Cycle detected walking parents from section "
                    f"{s.global_id!r} (section_id={s.section_id})"
                )
            # Find the parent in `out`.
            parent = out[pid - 1]  # IDs are 1-based dense indices.
            pid = parent.parent_id
            hops += 1

    return out
