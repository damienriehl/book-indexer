"""CUR-01 removal predicate — pure function for render-time entry suppression.

Per Phase 7 CONTEXT D-04: removal applies to the Subject Index in BOTH the
dual-locator and sections-only output variants. Tables of Cases / Statutes /
Rules are NOT subject to ``remove:`` — they're already curated by Phase 3b's
domain-specific normalizers.

Sub-entry inheritance: when a parent canonical is in the removal set, ALL its
sub-entries are also dropped. That inheritance is the CALLER'S responsibility
(this predicate operates per-entry; the caller — render/filter.py extension
in Wave 3 — walks the IndexTree and applies the predicate to each parent).

Dangling cross-reference cleanup is also the caller's responsibility: when a
removed term is the target of `See voir dire` or `(also: voir dire)`, those
cross-references are stripped from surviving entries during the render pass
(D-04). Test ``test_dangling_xref_clean.py`` (Wave 4) asserts.

The Phase 4 IR (``artifacts/index_tree.json``) is preserved untouched — this
is render-time projection only. Mirrors the B-05 cruft-filter pattern in
``render/filter.py:is_cruft``.

requirements_addressed: CUR-01.
"""
from __future__ import annotations


def is_removed(canonical: str, removal_set: frozenset[str]) -> bool:
    """Return True iff the canonical appears in the curator-confirmed removal set.

    Pure predicate — no I/O, no logging, no side effects. Comparison is
    case-sensitive: the curator fixture stores canonicals exactly as the LLM
    proposed and the author confirmed; case drift is handled separately by
    the recapitalize pass (CUR-02), not here.

    Args:
        canonical: the IndexEntry canonical string under test.
        removal_set: ``frozenset`` of canonicals the curator has confirmed
            for removal (typically ``CuratorOverrides.removal_set``).

    Returns:
        ``True`` if the entry should be dropped at render time, else ``False``.
    """
    return canonical in removal_set
