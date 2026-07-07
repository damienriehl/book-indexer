"""Per-chapter eyecite ``resolve_citations`` wrapper.

Per CONTEXT D-08, eyecite's ``resolve_citations`` runs ONCE per ``§ N``
chapter; state resets at each chapter boundary. Orphan ``Id.`` /
``Supra.`` citations (no preceding full cite within the chapter) are
logged to provenance and dropped from the table.

On the reference corpus the unresolved list is empty (RESEARCH §H-1: 0
IdCitation, 0 SupraCitation), but the contract is in place for the
companion volumes (Pretrial Litigation, Trial Advocacy).

Plan 03's ``__main__.py`` calls ``resolve_chapter()`` once per chapter and
aggregates the unresolved-orphan records into ``TableProvenance``.
"""
from __future__ import annotations

from dataclasses import dataclass

from eyecite import get_citations, resolve_citations
from eyecite.models import IdCitation, SupraCitation


@dataclass(frozen=True)
class UnresolvedCiteRecord:
    """A single orphan short-form citation that ``resolve_citations``
    could not bind to a full cite within this chapter.

    Logged to ``TableProvenance.unresolved_short_cites`` for transparency;
    dropped from the final tables (cannot be cited without an anchor).
    """

    chunk_id: str
    # Lock #1 note: ``pdf_page`` is a field name on this DROP-LIST record,
    # not a constructed locator. The record is provenance-only and never
    # reaches a Locator.
    pdf_page: int
    char_offset: int
    matched_text: str
    kind: str


def resolve_chapter(
    chapter_text: str,
    chunk_id: str,
    *,
    base_pdf_page: int = 0,
) -> tuple[dict, list[UnresolvedCiteRecord]]:
    """Run ``get_citations`` + ``resolve_citations`` over one chapter.

    Returns:
        (resolved_dict, unresolved_list) where:
        * ``resolved_dict`` is eyecite's ``Resource → list[Citation]`` mapping.
        * ``unresolved_list`` holds one ``UnresolvedCiteRecord`` per orphan
          ``Id.`` or ``Supra.`` citation (i.e., short-form cites that did
          NOT end up in any resolved-group's value list).

    Per D-08: each call is independent. Two consecutive calls on
    independent texts do NOT share state — state is implicit in the
    eyecite ``resolve_citations`` call's local closure.
    """
    if not chapter_text:
        return {}, []

    cs = get_citations(chapter_text)
    resolved = resolve_citations(cs)

    # Build identity-set of every Citation that landed inside any
    # resolved group. Identity (``id(c)``) is correct here because
    # eyecite returns the SAME Citation instances inside both ``cs`` and
    # the resolved-group value lists.
    in_resolved: set[int] = set()
    for group_cites in resolved.values():
        for c in group_cites:
            in_resolved.add(id(c))

    unresolved: list[UnresolvedCiteRecord] = []
    for c in cs:
        if not isinstance(c, (IdCitation, SupraCitation)):
            continue
        if id(c) in in_resolved:
            continue
        span = c.span()
        unresolved.append(
            UnresolvedCiteRecord(
                chunk_id=chunk_id,
                pdf_page=base_pdf_page,
                char_offset=span[0] if span else 0,
                matched_text=c.matched_text() or "",
                kind=type(c).__name__,
            )
        )

    return resolved, unresolved
