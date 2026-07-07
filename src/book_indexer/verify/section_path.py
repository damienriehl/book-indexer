"""Parent-walk resolver — returns the section_ref chain for an evidence row.

Phase 1's `sections` table has 4 levels (0=Chapter, 1=§N, 2=N.NN, 3=N.NN.M).
Phase 2's Evidence contract excludes level 0 (RESEARCH §Pitfall 8): the
chapter is implicit in the section tree, never part of the printed locator.

Returns the path root-first leaf-last: `["§2", "§2.04", "§2.04.1"]`. The last
element is always the deepest section (== Evidence.section_ref). Level-0
ancestors are filtered out.

Defensive depth cap: the walk stops after 5 hops (section tree has ≤4 levels
plus one safety margin) — a longer walk indicates a cycle or malformed
parent_id chain, which raises VerifierError.
"""
from __future__ import annotations

import sqlite3

from .errors import VerifierError


def resolve_section_path(conn: sqlite3.Connection, section_id: int) -> list[str]:
    """Walk sections.parent_id from section_id up to the level-1 root.

    Returns section_refs in root-to-leaf order (level 1 → level N).
    Excludes level-0 (Chapter) per RESEARCH §Pitfall 8.
    Raises VerifierError on cycle or depth > 5.
    """
    path: list[str] = []
    cur: int | None = section_id
    seen: set[int] = set()
    hops = 0

    while cur is not None:
        if cur in seen:
            raise VerifierError(
                f"cycle in sections.parent_id starting at section_id={section_id}"
            )
        seen.add(cur)
        row = conn.execute(
            "SELECT section_ref, section_level, parent_id FROM sections "
            "WHERE section_id = ?",
            (cur,),
        ).fetchone()
        if row is None:
            break
        ref, level, parent_id = row
        if level >= 1:  # skip level 0 (Chapter) per RESEARCH Pitfall 8
            path.append(ref)
        cur = parent_id
        hops += 1
        if hops > 5:
            raise VerifierError(
                f"section_path walk exceeded 5 hops at section_id={section_id}"
            )
    return list(reversed(path))  # root-first leaf-last
