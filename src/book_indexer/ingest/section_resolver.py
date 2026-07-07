"""Section boundary resolver + monotonicity gate (Plan 01-03-b Task 3b.2).

Converts the linear stream of :class:`SectionStart` events emitted by
:class:`SectionDetector` into closed, disjoint ``(pdf_page, token_offset)``
intervals — one interval per section — by pairing each event with the next
same-or-higher-level event.

Chapter-scoped numbering: the source book restarts section numbering at each
chapter, so refs like ``§1.01`` appear five times in the book. We carry the
enclosing chapter (from level-0 ``Chapter N`` events) alongside each section
to make ``(chapter, ref)`` globally unique. Parent edges are built in
:mod:`section_tree`.

Monotonicity (D-26, SEC-08): after resolution, :func:`check_section_monotonicity`
asserts that within every ``(chapter, § N)`` scope the level-2 ``N.NN`` values
strictly increase, and within every ``(chapter, § N, N.NN)`` scope the level-3
``.M`` values strictly increase. Violations raise
:class:`SectionMonotonicityError`.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from .errors import SectionMonotonicityError
from .section_detector import SectionStart

# Regexes for parsing refs produced by SectionDetector.
_LEVEL_1_RE = re.compile(r"^§(\d+)$")
_LEVEL_2_RE = re.compile(r"^§(\d+)\.(\d{2})$")
_LEVEL_3_RE = re.compile(r"^§(\d+)\.(\d{2})\.(\d+)$")
_LEVEL_0_RE = re.compile(r"^Chapter\s+(\d+)$")


@dataclass(frozen=True)
class Section:
    """Resolved section with closed ``[start, end]`` interval.

    ``section_id`` is assigned by the corpus writer (plan 01-04) at
    persistence time; the resolver sets it to ``-1`` as a sentinel.
    ``start_folio`` is filled in by the corpus writer from the folio fixture;
    the resolver leaves it empty.
    """
    section_id: int
    section_ref: str
    section_level: int
    chapter: int
    global_id: str
    parent_id: int | None
    parent_global_id: str | None
    title: str
    start_pdf_page: int
    start_token_offset: int
    end_pdf_page: int
    end_token_offset: int
    start_folio: str = ""

    @classmethod
    def from_start(
        cls,
        start: SectionStart,
        chapter: int,
        parent_global_id: str | None,
        end_pdf_page: int,
        end_token_offset: int,
    ) -> "Section":
        return cls(
            section_id=-1,
            section_ref=start.section_ref,
            section_level=start.section_level,
            chapter=chapter,
            global_id=_global_id(chapter, start.section_ref),
            parent_id=None,
            parent_global_id=parent_global_id,
            title=start.title,
            start_pdf_page=start.pdf_page,
            start_token_offset=start.token_offset,
            end_pdf_page=end_pdf_page,
            end_token_offset=end_token_offset,
        )


def _global_id(chapter: int, ref: str) -> str:
    """``ch{N}:{ref}`` — matches fixture convention (01-01 SUMMARY)."""
    return f"ch{chapter}:{ref}"


class SectionResolver:
    """Stateless boundary resolver.

    Given the linear stream of detected :class:`SectionStart` events across all
    PDF pages, produce three disjoint interval partitions of the body-token
    stream — one per level (1, 2, 3) — with ``end`` computed as the next
    same-or-higher-level event's position minus one token, or the book-end
    marker for the last section at each level. Level-0 (Chapter) sections
    follow the same rule, bounded by the next chapter or the book end.
    """

    def resolve(
        self,
        section_starts: list[SectionStart],
        book_end_marker: tuple[int, int],
    ) -> list[Section]:
        """Compute intervals for every section.

        Args:
            section_starts: events in any order — we sort by position.
            book_end_marker: ``(last_pdf_page, last_body_token_offset + 1)``
                — the open upper bound for sections that run to book end.

        Returns:
            list of :class:`Section` sorted by ``(start_pdf_page,
            start_token_offset)``. Levels interleave; callers filter by
            ``section_level`` if they need one partition.
        """
        if not section_starts:
            return []

        # Sort events by position. Equal positions: level 0 before 1 before 2
        # before 3, so chapter-start pages have their chapter emitted first.
        sorted_events = sorted(
            section_starts,
            key=lambda e: (e.pdf_page, e.token_offset, e.section_level),
        )

        # Track the enclosing chapter (level-0 ref) and active level-1 / level-2
        # as we stream through events, so each section knows its parent-by-ref.
        current_chapter_ref: str | None = None
        current_chapter_n: int = 0
        active_level_1_gid: str | None = None
        active_level_2_gid: str | None = None

        # First pass: assign chapter numbering + parent global_ids per event.
        enriched: list[tuple[SectionStart, int, str | None]] = []
        for ev in sorted_events:
            if ev.section_level == 0:
                m = _LEVEL_0_RE.match(ev.section_ref)
                if m:
                    current_chapter_n = int(m.group(1))
                    current_chapter_ref = ev.section_ref
                    active_level_1_gid = None
                    active_level_2_gid = None
                enriched.append((ev, current_chapter_n, None))
                continue

            if ev.section_level == 1:
                parent_gid = (
                    _global_id(current_chapter_n, current_chapter_ref)
                    if current_chapter_ref is not None
                    else None
                )
                enriched.append((ev, current_chapter_n, parent_gid))
                active_level_1_gid = _global_id(current_chapter_n, ev.section_ref)
                active_level_2_gid = None
                continue

            if ev.section_level == 2:
                parent_gid = active_level_1_gid
                enriched.append((ev, current_chapter_n, parent_gid))
                active_level_2_gid = _global_id(current_chapter_n, ev.section_ref)
                continue

            if ev.section_level == 3:
                # Derive parent from N.NN prefix of the ref; fall back to active
                # level-2 if the prefix doesn't resolve (defensive).
                m = _LEVEL_3_RE.match(ev.section_ref)
                if m:
                    major_ref = f"§{int(m.group(1))}.{int(m.group(2)):02d}"
                    parent_gid = _global_id(current_chapter_n, major_ref)
                else:
                    parent_gid = active_level_2_gid
                enriched.append((ev, current_chapter_n, parent_gid))
                continue

            # Unknown level: keep the event but no parent.
            enriched.append((ev, current_chapter_n, None))

        # Second pass: compute ``end`` per event as the position of the next
        # event at the SAME level (for any level 0..3), minus one token.
        # Rationale: a level-2 section ends when the next level-2 begins, or
        # the enclosing level-1 ends. Using "same level" is simpler and
        # produces disjoint intervals within each level.
        sections: list[Section] = []
        last_page, last_offset = book_end_marker

        for i, (ev, chap, parent_gid) in enumerate(enriched):
            # Find next event at the SAME level (within the same chapter for
            # levels 1-3, to respect chapter-scoped numbering).
            end_pdf_page = last_page
            end_token_offset = last_offset
            for j in range(i + 1, len(enriched)):
                nxt, nxt_chap, _ = enriched[j]
                if nxt.section_level == ev.section_level:
                    if ev.section_level == 0 or nxt_chap == chap:
                        end_pdf_page = nxt.pdf_page
                        end_token_offset = nxt.token_offset
                        break
                # For level 0 we never restrict by chapter.
                if ev.section_level == 0 and nxt.section_level == 0:
                    end_pdf_page = nxt.pdf_page
                    end_token_offset = nxt.token_offset
                    break

            sections.append(Section.from_start(
                ev, chapter=chap, parent_global_id=parent_gid,
                end_pdf_page=end_pdf_page,
                end_token_offset=end_token_offset,
            ))

        return sections


# ------------------------------------------------------------------
# Monotonicity gate (D-26, SEC-08)
# ------------------------------------------------------------------


def check_section_monotonicity(sections: list[Section]) -> None:
    """Chapter-scoped strict monotonicity. Raises
    :class:`SectionMonotonicityError` on any violation.

    the source book restarts section numbering at each chapter, so:
    - Within each ``(chapter, § N)`` scope, level-2 ``N.NN`` values must
      strictly increase across events in PDF-reading order.
    - Within each ``(chapter, § N, N.NN)`` scope, level-3 ``.M`` values must
      strictly increase.

    Level-0 (Chapter) monotonicity is enforced globally (Chapter 1 < 2 < …).
    """
    # Level-0 global monotonicity.
    chapters: list[tuple[int, int]] = []  # (chapter_n, pdf_page)
    for s in sections:
        if s.section_level == 0:
            m = _LEVEL_0_RE.match(s.section_ref)
            if m:
                chapters.append((int(m.group(1)), s.start_pdf_page))
    for i in range(1, len(chapters)):
        if chapters[i][0] <= chapters[i - 1][0]:
            raise SectionMonotonicityError(
                f"Chapter monotonicity violated: Chapter {chapters[i - 1][0]} "
                f"at p.{chapters[i - 1][1]} followed by Chapter {chapters[i][0]} "
                f"at p.{chapters[i][1]}"
            )

    # Level-1 within-chapter monotonicity.
    by_chapter_l1: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for s in sections:
        if s.section_level != 1:
            continue
        m = _LEVEL_1_RE.match(s.section_ref)
        if not m:
            continue
        by_chapter_l1[s.chapter].append((int(m.group(1)), s.start_pdf_page))
    for chap_n, seq in by_chapter_l1.items():
        for i in range(1, len(seq)):
            if seq[i][0] <= seq[i - 1][0]:
                raise SectionMonotonicityError(
                    f"Section monotonicity violated in ch{chap_n}: "
                    f"§{seq[i - 1][0]} at p.{seq[i - 1][1]} "
                    f"followed by §{seq[i][0]} at p.{seq[i][1]}"
                )

    # Level-2 within-chapter-section monotonicity.
    by_chap_sec_l2: dict[tuple[int, str], list[tuple[int, int]]] = defaultdict(list)
    current_chap_sec: dict[int, str | None] = defaultdict(lambda: None)
    for s in sections:
        if s.section_level == 1:
            current_chap_sec[s.chapter] = s.section_ref
            continue
        if s.section_level != 2:
            continue
        chap_sec = current_chap_sec.get(s.chapter) or ""
        m = _LEVEL_2_RE.match(s.section_ref)
        if not m:
            continue
        scope = (s.chapter, chap_sec)
        by_chap_sec_l2[scope].append((int(m.group(2)), s.start_pdf_page))
    for (chap_n, chap_sec), seq in by_chap_sec_l2.items():
        for i in range(1, len(seq)):
            if seq[i][0] <= seq[i - 1][0]:
                raise SectionMonotonicityError(
                    f"Section monotonicity violated in ch{chap_n} {chap_sec}: "
                    f"§{seq[i - 1][0]:02d} at p.{seq[i - 1][1]} "
                    f"followed by §{seq[i][0]:02d} at p.{seq[i][1]}"
                )

    # Level-3 within-major-section monotonicity.
    by_major_l3: dict[tuple[int, str], list[tuple[int, int]]] = defaultdict(list)
    current_major_ref: dict[int, str | None] = defaultdict(lambda: None)
    for s in sections:
        if s.section_level == 2:
            current_major_ref[s.chapter] = s.section_ref
            continue
        if s.section_level != 3:
            continue
        major = current_major_ref.get(s.chapter) or ""
        m = _LEVEL_3_RE.match(s.section_ref)
        if not m:
            continue
        scope = (s.chapter, major)
        by_major_l3[scope].append((int(m.group(3)), s.start_pdf_page))
    for (chap_n, major), seq in by_major_l3.items():
        for i in range(1, len(seq)):
            if seq[i][0] <= seq[i - 1][0]:
                raise SectionMonotonicityError(
                    f"Sub-section monotonicity violated in ch{chap_n} {major}: "
                    f".{seq[i - 1][0]} at p.{seq[i - 1][1]} "
                    f"followed by .{seq[i][0]} at p.{seq[i][1]}"
                )
