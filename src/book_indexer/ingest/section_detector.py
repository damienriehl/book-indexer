"""Deterministic section-heading detector (Plan 01-03-b, SEC-01..SEC-06).

Empirical typography for the reference corpus (per Wave-0 probe of the source PDF):

| Level | Name       | Font name                  | Size    | Body signature           | Regex                               |
|-------|------------|----------------------------|---------|--------------------------|-------------------------------------|
| 0     | Chapter    | ``CenturySchoolbook-Bold`` | 18.00pt | ``CHAPTER N`` all-caps   | ``^CHAPTER\\s+(\\d+)``              |
| 1     | ``§ N``    | ``CenturySchoolbook-Bold`` | 13.02pt | ``§ N.``                 | ``^§\\s*(\\d+)\\.?$``               |
| 2     | ``N.NN``   | ``CenturySchoolbook-Bold`` | 10.98pt | ``N.NN`` alone in span   | ``^(\\d{1,2})\\.(\\d{2})\\s*$``     |
| 3     | ``N.NN.M`` | ``CenturySchoolbook-BoldIt`` | 10.98pt | ``N.NN.M Title`` merged  | ``^(\\d{1,2})\\.(\\d{2})\\.(\\d{1,2})(?:\\s+(.*))?$`` |
|   —   | body       | ``CenturySchoolbook`` plain | ~10.5pt | prose                    | —                                   |

NOTE: the pre-pivot RESEARCH.md §SEC.1 recorded level-1 at 10.50pt based on a
running-head observation. The Wave-0 empirical probe on chapter-start pages
showed that the level-1 heading where a new § N *begins* is 13.02pt Bold —
while the 10.50pt `§ N` in the running head of *subsequent* pages is the
active-chapter-section marker, not a new section start. This detector emits
level-1 SectionStart events on the 13.02pt body signature only; running-head
tracking lives in the header-strip pass (plan 01-02).

Detection is gated on ``(font_name, font_size, regex)`` triple; no fuzzy
signals, no cross-span heuristics other than same-line title pickup. TOC
entries (plain ``CenturySchoolbook`` 10.02pt) and body prose (plain
``CenturySchoolbook`` 10.5pt) are rejected by the font-name gate. This is
defense-in-depth (D-22): even if a ``§`` character appeared in regular body
prose, the font gate would disqualify it.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

# ------------------------------------------------------------------
# Regex patterns — module-scope constants (per plan 01-03-b Task 3b.1).
# ------------------------------------------------------------------

#: Level 2 major-section ref: ``2.04`` (chapter 2, major 04) possibly with a
#: trailing space before the title span. Anchored; two-digit major required.
SECTION_2_RE = re.compile(r"^(\d{1,2})\.(\d{2})(?:\s|$)")

#: Level 3 sub-section ref: ``2.04.1`` with optional trailing title on the same
#: span. Anchored; two-digit major, one-or-two-digit sub.
SECTION_3_RE = re.compile(r"^(\d{1,2})\.(\d{2})\.(\d{1,2})(?:\s|$)")

#: Level 1 chapter-section ref: ``§ 1`` or ``§ 1.`` — trailing dot tolerated
#: because the body emits ``§ N.`` while the running head emits ``§ N``.
CHAPTER_SEC_RE = re.compile(r"^§\s*(\d+)\s*\.?\s*$")

#: Level 0 chapter ref: ``CHAPTER 1`` where the first letter ``C`` is a
#: drop-cap at 18pt. The ``_chapter_heading_re`` on the assembled line matches
#: the concatenated caps.
CHAPTER_RE = re.compile(r"^CHAPTER\s+(\d+)\b", re.IGNORECASE)

# Typography triggers — floating-point tolerance matches PyMuPDF's rounding.
_SIZE_TOL = 0.05
_SIZE_LEVEL_0 = 18.00
_SIZE_LEVEL_1 = 13.02
_SIZE_LEVEL_2_3 = 10.98
_SIZE_RUNNING_HEAD = 10.50

_FONT_BOLD = "CenturySchoolbook-Bold"
_FONT_BOLDIT = "CenturySchoolbook-BoldIt"


# ------------------------------------------------------------------
# Data class
# ------------------------------------------------------------------


@dataclass(frozen=True)
class SectionStart:
    """One section-start event emitted by :class:`SectionDetector`.

    Attributes mirror the fixture schema (``ref``, ``level``, ``title``) plus
    the location pinning needed by the boundary resolver.
    """
    section_ref: str
    section_level: int
    pdf_page: int
    token_offset: int
    title: str
    source: Literal["body", "running_head"]


# ------------------------------------------------------------------
# Detector
# ------------------------------------------------------------------


class SectionDetector:
    """Scan a page's ``get_text("dict")`` output and yield
    :class:`SectionStart` events in reading order.

    The detector is stateless; callers sequence it over all pages and pass in
    ``active_chapter_section`` so level-2 / level-3 events know which level-1
    parent they live under (though the detector does not use that fact — it is
    the tree builder's job in :mod:`section_tree`).
    """

    def detect_sections(
        self,
        page_dict: dict,
        pdf_page: int,
        *,
        active_chapter_section: str | None = None,
        active_chapter: str | None = None,
    ) -> Iterator[SectionStart]:
        """Yield every section-start on one PDF page in reading order.

        Args:
            page_dict: PyMuPDF ``get_text("dict")`` output for the page.
            pdf_page: 0-indexed PDF page number (extractor convention).
            active_chapter_section: current ``§ N`` ref, for human-readable
                logging only; the boundary resolver re-derives parents.
            active_chapter: current chapter ref (e.g. ``"Chapter 1"``); same
                contract — logging only.

        Yields:
            :class:`SectionStart` per detected heading. Emission order within
            a page is the PyMuPDF block-reading order, which the extractor
            already sorts deterministically by ``(y, x)`` (see
            :mod:`extractor.block_reading_order`).
        """
        # Unused params retained for API symmetry with caller's state tracking.
        del active_chapter_section, active_chapter

        token_offset = 0
        all_blocks = page_dict.get("blocks", [])
        for block_idx, block in enumerate(all_blocks):
            if block.get("type") != 0:  # type 0 = text block
                continue
            lines = block.get("lines", [])
            for line_idx, line in enumerate(lines):
                spans = line.get("spans", [])
                if not spans:
                    continue

                # Pre-compute the assembled line text for Level-0 / fallback
                # detection; level-0 has many 18pt and 14.52pt drop-cap spans
                # on one line that concatenate to "CHAPTER N ".
                line_text = "".join(s.get("text", "") for s in spans).strip()

                # Priority: check level-0 first (single big heading per page)
                # before scanning individual spans for levels 1/2/3.
                if self._is_chapter_heading(spans, line_text):
                    m = CHAPTER_RE.match(line_text)
                    if m:
                        chap_n = int(m.group(1))
                        title = self._pick_chapter_title(
                            block, line, all_blocks, block_idx,
                        )
                        yield SectionStart(
                            section_ref=f"Chapter {chap_n}",
                            section_level=0,
                            pdf_page=pdf_page,
                            token_offset=token_offset,
                            title=title,
                            source="body",
                        )
                        token_offset += 1
                        continue  # done with this line

                # Levels 1, 2, 3 are single-span detections with title pickup.
                for i, span in enumerate(spans):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue
                    font_name = span.get("font", "")
                    font_size = round(float(span.get("size", 0.0)), 2)

                    # Level 3 FIRST (SECTION_3_RE would otherwise be masked by
                    # SECTION_2_RE matching on the `N.NN` prefix of `N.NN.M`).
                    if (
                        font_name == _FONT_BOLDIT
                        and abs(font_size - _SIZE_LEVEL_2_3) < _SIZE_TOL
                    ):
                        if m := SECTION_3_RE.match(text):
                            chap = int(m.group(1))
                            maj = int(m.group(2))
                            sub = int(m.group(3))
                            ref = f"§{chap}.{maj:02d}.{sub}"
                            title = self._pick_title(
                                spans, i, text, m.end(),
                                block, line_idx,
                                all_blocks, block_idx,
                                expected_font=_FONT_BOLDIT,
                                expected_size=_SIZE_LEVEL_2_3,
                            )
                            yield SectionStart(
                                section_ref=ref,
                                section_level=3,
                                pdf_page=pdf_page,
                                token_offset=token_offset,
                                title=title,
                                source="body",
                            )
                            token_offset += 1
                            continue

                    # Level 2
                    if (
                        font_name == _FONT_BOLD
                        and abs(font_size - _SIZE_LEVEL_2_3) < _SIZE_TOL
                    ):
                        if m := SECTION_2_RE.match(text):
                            chap = int(m.group(1))
                            maj = int(m.group(2))
                            ref = f"§{chap}.{maj:02d}"
                            title = self._pick_title(
                                spans, i, text, m.end(),
                                block, line_idx,
                                all_blocks, block_idx,
                                expected_font=_FONT_BOLD,
                                expected_size=_SIZE_LEVEL_2_3,
                            )
                            yield SectionStart(
                                section_ref=ref,
                                section_level=2,
                                pdf_page=pdf_page,
                                token_offset=token_offset,
                                title=title,
                                source="body",
                            )
                            token_offset += 1
                            continue

                    # Level 1 — body signature (13.02pt Bold on the chapter-start
                    # or §-N-start page). Ignores the 10.50pt running-head
                    # variant: that is a marker for header-strip / active-
                    # chapter-section tracking, not a new section boundary.
                    if (
                        font_name == _FONT_BOLD
                        and abs(font_size - _SIZE_LEVEL_1) < _SIZE_TOL
                    ):
                        if m := CHAPTER_SEC_RE.match(text):
                            chap = int(m.group(1))
                            ref = f"§{chap}"
                            title = self._pick_title(
                                spans, i, text, m.end(),
                                block, line_idx,
                                all_blocks, block_idx,
                                expected_font=_FONT_BOLD,
                                expected_size=_SIZE_LEVEL_1,
                            )
                            yield SectionStart(
                                section_ref=ref,
                                section_level=1,
                                pdf_page=pdf_page,
                                token_offset=token_offset,
                                title=title,
                                source="body",
                            )
                            token_offset += 1
                            continue

                    token_offset += 1

    # ------------------------------------------------------------------
    # Helpers — private
    # ------------------------------------------------------------------

    @staticmethod
    def _is_chapter_heading(spans: list[dict], line_text: str) -> bool:
        """Is this line the chapter drop-cap + caps heading?

        Signature (from empirical probe):
        - At least one span with font=CenturySchoolbook-Bold, size≈18.00pt;
        - The assembled line text matches ``^CHAPTER\\s+\\d+``.
        """
        has_18pt_bold = any(
            s.get("font") == _FONT_BOLD
            and abs(round(float(s.get("size", 0.0)), 2) - _SIZE_LEVEL_0) < _SIZE_TOL
            for s in spans
        )
        return has_18pt_bold and CHAPTER_RE.match(line_text) is not None

    @staticmethod
    def _pick_chapter_title(
        block: dict,
        chapter_line: dict,
        all_blocks: list[dict] | None = None,
        block_idx: int | None = None,
    ) -> str:
        """Assemble the chapter title from lines/blocks following the ``CHAPTER N``
        line. Chapter titles span 1-3 lines of caps in ``CenturySchoolbook-Bold``
        at 14.52pt (regular caps) or 18.00pt (drop-caps).

        The PDF frequently places the chapter title in SEPARATE BLOCKS from
        the ``CHAPTER N`` block (PyMuPDF's block segmentation splits them by
        vertical gap). We scan:
        1. Lines in the same block after the chapter line, and
        2. Subsequent blocks whose every span is the same heading font + size,
           up to 3 contiguous blocks.

        Stops at the first non-heading-font line/block — e.g., the
        ``CenturySchoolbook-Bold 13.02pt`` ``§ N.`` section heading or the
        ``CenturySchoolbook-Bold 12.00pt`` ``A. SCOPE`` sub-header.
        """
        def _is_chapter_title_line(line: dict) -> tuple[bool, str]:
            spans = line.get("spans", [])
            if not spans:
                return False, ""
            heading_fonts = {
                (s.get("font"), round(float(s.get("size", 0.0)), 2))
                for s in spans
                if (s.get("text") or "").strip()
            }
            if not heading_fonts:
                return False, ""
            all_ok = all(
                fn == _FONT_BOLD and (abs(sz - 14.52) < _SIZE_TOL or abs(sz - 18.00) < _SIZE_TOL)
                for fn, sz in heading_fonts
            )
            if not all_ok:
                return False, ""
            joined = "".join(s.get("text", "") for s in spans).strip()
            return (True, joined) if joined else (False, "")

        out_parts: list[str] = []

        # 1) Lines in the SAME block after the chapter line.
        reached = False
        stopped_within_block = False
        for line in block.get("lines", []):
            if line is chapter_line:
                reached = True
                continue
            if not reached:
                continue
            ok, txt = _is_chapter_title_line(line)
            if not ok:
                stopped_within_block = True
                break
            out_parts.append(txt)

        # 2) Subsequent blocks (at most 3) with heading-only lines.
        if not stopped_within_block and all_blocks is not None and block_idx is not None:
            cont_blocks = 0
            for b_k in range(block_idx + 1, len(all_blocks)):
                if cont_blocks >= 3:
                    break
                nxt_block = all_blocks[b_k]
                if nxt_block.get("type") != 0:
                    break
                nxt_lines = nxt_block.get("lines", [])
                if not nxt_lines:
                    break
                block_ok = True
                block_parts: list[str] = []
                for ln in nxt_lines:
                    ok, txt = _is_chapter_title_line(ln)
                    if not ok:
                        block_ok = False
                        break
                    block_parts.append(txt)
                if not block_ok or not block_parts:
                    break
                out_parts.extend(block_parts)
                cont_blocks += 1

        raw = " ".join(out_parts).strip()
        raw = " ".join(raw.split())  # collapse whitespace runs
        return _titlecase_caps(raw)

    @staticmethod
    def _pick_title(
        spans: list[dict],
        after_idx: int,
        current_span_text: str,
        regex_end: int,
        block: dict,
        line_idx: int,
        all_blocks: list[dict],
        block_idx: int,
        *,
        expected_font: str,
        expected_size: float,
    ) -> str:
        """Assemble the heading title with four fallbacks:

        1. **Embedded**: text on the current span AFTER the ref regex.
           (Level 3 often has ``"1.01.1 How to Be an Advocate "`` in one span.)
        2. **Same-line tail**: text from subsequent spans on the same line.
           (Occurs when title starts mid-line after an Arial-glue space span.)
        3. **Next-line-in-block**: text from the next line(s) in the SAME
           block, stopping at a non-heading-font line. (Level 1 / level 2
           headings commonly wrap the title to a separate line at the same
           y-position within the block: ``"1.01" / "Why Be an Advocate?"``.)
        4. **Next-block continuation**: text from the next BLOCK(s) if they
           use the same heading font + size. PyMuPDF sometimes splits a
           heading's continuation into a separate block when layout spacing
           exceeds its default line-merge threshold. (Example:
           ``"3.20.6 Motion for Judgment as Matter of Law, or Directed "``
           in one block, ``"Verdict, or to Dismiss "`` in the next block.)
        """
        combined: list[str] = []

        # 1) Embedded tail on the trigger span.
        embedded = current_span_text[regex_end:].strip()
        if embedded:
            combined.append(embedded)

        # 2) Same-line tail spans.
        tail_parts: list[str] = []
        for j in range(after_idx + 1, len(spans)):
            t = spans[j].get("text", "")
            if t:
                tail_parts.append(t)
        tail_same_line = " ".join(tail_parts).strip()
        if tail_same_line:
            combined.append(tail_same_line)

        # 3) Next-line-in-block continuation — fires whenever lines after
        #    the trigger line use the SAME heading font + size. Stops at
        #    the first line in a different font/size, a blank line, or a
        #    line whose text starts with another ref (defense).
        def _line_is_heading_continuation(line: dict) -> tuple[bool, str]:
            line_spans = line.get("spans", [])
            if not line_spans:
                return False, ""
            for s in line_spans:
                sfont = s.get("font", "")
                ssize = round(float(s.get("size", 0.0)), 2)
                # Whitespace-only spans may land in a different font (e.g.,
                # a trailing space in the body font or Arial-glue) — allow
                # them; they contribute nothing semantic.
                if not (s.get("text") or "").strip():
                    continue
                if sfont != expected_font or abs(ssize - expected_size) >= _SIZE_TOL:
                    return False, ""
            ln_text = "".join(s.get("text", "") for s in line_spans).strip()
            if not ln_text:
                return False, ""
            # Defense: don't pull a new ref into this title.
            if (SECTION_2_RE.match(ln_text) or SECTION_3_RE.match(ln_text)
                    or CHAPTER_SEC_RE.match(ln_text)):
                return False, ""
            return True, ln_text

        lines = block.get("lines", [])
        stopped_within_block = False
        for k in range(line_idx + 1, len(lines)):
            ok, txt = _line_is_heading_continuation(lines[k])
            if not ok:
                stopped_within_block = True
                break
            combined.append(txt)

        # 4) Next-block continuation — only attempt if we did NOT stop inside
        #    the current block on a non-heading line (which signals the title
        #    is already finished by body prose). Limited to at most 2 sibling
        #    blocks to avoid unbounded runaway merging.
        if not stopped_within_block:
            cont_blocks = 0
            for b_k in range(block_idx + 1, len(all_blocks)):
                if cont_blocks >= 2:
                    break
                nxt_block = all_blocks[b_k]
                if nxt_block.get("type") != 0:
                    break
                nxt_lines = nxt_block.get("lines", [])
                if not nxt_lines:
                    break
                # All lines in this block must be heading-font.
                block_ok = True
                block_parts: list[str] = []
                for ln in nxt_lines:
                    ok, txt = _line_is_heading_continuation(ln)
                    if not ok:
                        block_ok = False
                        break
                    block_parts.append(txt)
                if not block_ok or not block_parts:
                    break
                combined.extend(block_parts)
                cont_blocks += 1

        # Join accumulated parts carefully: if the previous chunk ends with
        # an en/em-dash or hyphen, do NOT add a space before the next chunk.
        # (Example: ``"FRE 702–"`` + ``"705)"`` should join as
        # ``"FRE 702–705)"``, not ``"FRE 702– 705)"``.)
        joined = ""
        for part in combined:
            if not joined:
                joined = part
                continue
            # Normalize inner whitespace in each part.
            part = " ".join(part.split())
            if not part:
                continue
            if joined.endswith(("-", "–", "—")):
                joined = joined + part
            else:
                joined = joined + " " + part
        out = " ".join(joined.split()).strip()
        return out


# ------------------------------------------------------------------
# Title-case helper
# ------------------------------------------------------------------


_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on",
    "or", "so", "the", "to", "up", "yet",
}


def _titlecase_caps(s: str) -> str:
    """Convert ALL-CAPS heading text to Title Case.

    The PDF renders chapter titles in all-caps as display styling — but the
    fixture + human expectation is Title Case (``Planning to Win: Effective
    Preparation``). We detect an all-caps string (≥70% letters uppercase) and
    convert; otherwise we return the string untouched.
    """
    if not s:
        return s
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return s
    upper_ratio = sum(1 for c in letters if c.isupper()) / max(len(letters), 1)
    if upper_ratio < 0.7:
        return s

    # Split by whitespace but preserve punctuation attached to words.
    words = s.split()
    out: list[str] = []
    for idx, w in enumerate(words):
        lw = w.lower()
        # Keep simple punctuation separators unchanged.
        if all(not c.isalpha() for c in w):
            out.append(w)
            continue
        stripped = w.strip(",.:;!?")
        punct_tail = w[len(stripped):] if len(stripped) < len(w) else ""
        base = stripped.lower()
        is_first = idx == 0
        is_after_colon = idx > 0 and out and out[-1].endswith(":")
        if not is_first and not is_after_colon and base in _SMALL_WORDS:
            out.append(base + punct_tail)
        else:
            out.append(base.capitalize() + punct_tail)
        # Ensure 'I' and any other one-letter word we miss stay capitalized.
        _ = lw
    return " ".join(out)
