"""Header/footer stripping (D-07, D-08, D-09).

Two-stage:
  1. Coordinate-band strip (based on detected YBands).
  2. Cross-page repetition detection within top/bottom bands (>=70% threshold).

Chapter-title running heads are stripped from body but recorded as `page_section`
on the corresponding `pages` row (D-08 handled by downstream caller). The
`§ N` chapter-section token captured in the top band is surfaced as the page's
`active_chapter_section` via :func:`extract_active_chapter_section`; plan 01-03-b
consumes this for level-0 Chapter anchors.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import cast

from .normalizer import normalize
from .types import PageExtraction, StrippedBlock, YBands

REPETITION_THRESHOLD = 0.70  # D-07: >= 70% cross-page repetition => running head

# Matches `§ N` (with optional whitespace around N). Captures the numeric part.
_CHAPTER_SECTION_RE = re.compile(r"§\s*(\d+)")


def _block_text(block: dict) -> str:
    parts: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
    return "".join(parts)


def _block_position(block: dict, page_width: float) -> str:
    """Classify x-position as left / center / right."""
    x0, _, x1, _ = block["bbox"]
    center_x = (x0 + x1) / 2.0
    if center_x < page_width / 3.0:
        return "left"
    if center_x > page_width * 2.0 / 3.0:
        return "right"
    return "center"


def _collect_top_band_blocks(ex: PageExtraction, y_bands: YBands) -> list[dict]:
    """Return the text blocks from the page's top strip band (y0 < top_cutoff)."""
    return [
        b
        for b in ex.dict_output["blocks"]
        if b["type"] == 0 and float(b["bbox"][1]) < y_bands.top_cutoff
    ]


def extract_active_chapter_section(
    extractions: Sequence[PageExtraction],
    y_bands: YBands,
) -> dict[int, str | None]:
    """Per-page `active_chapter_section` derived from `§ N` running-head tokens (D-08).

    Plan 01-03-b's section-tree builder consumes this to anchor level-0 Chapter
    entries. A page inherits the most recent prior page's value when its own top
    band has no `§ N` token (e.g., chapter-start pages where the running head is
    suppressed).
    """
    active: dict[int, str | None] = {}
    last_seen: str | None = None
    for ex in sorted(extractions, key=lambda x: x.pdf_page):
        top_blocks = _collect_top_band_blocks(ex, y_bands)
        found: str | None = None
        for b in top_blocks:
            text = _block_text(b)
            m = _CHAPTER_SECTION_RE.search(text)
            if m:
                found = f"§ {m.group(1)}"
                break
        if found is not None:
            last_seen = found
        active[ex.pdf_page] = last_seen
    return active


def strip_headers_footers(
    extractions: Sequence[PageExtraction],
    y_bands: YBands,
) -> tuple[list[StrippedBlock], dict[int, set[int]]]:
    """Return (audit_rows, stripped_block_indices_by_page).

    The second return value maps pdf_page -> set of block indices that were stripped.
    Callers use it to filter the in-memory block list before tokenization.
    """
    # Phase 1: coordinate-band strip.
    band_candidates: dict[int, list[tuple[int, dict, str]]] = defaultdict(list)
    for ex in extractions:
        blocks = [b for b in ex.dict_output["blocks"] if b["type"] == 0]
        for idx, b in enumerate(blocks):
            y0 = float(b["bbox"][1])
            y1 = float(b["bbox"][3])
            if y0 < y_bands.top_cutoff:
                band_candidates[ex.pdf_page].append((idx, b, "top"))
            elif y1 > y_bands.bot_cutoff:
                band_candidates[ex.pdf_page].append((idx, b, "bottom"))

    audit: list[StrippedBlock] = []
    stripped: dict[int, set[int]] = defaultdict(set)

    # Every coordinate-band block is stripped with reason="regex" or "repetition"
    # depending on whether its normalized text repeats across >=70% of pages.
    total_pages = len(extractions)
    norm_counts: Counter[str] = Counter()
    for page_blocks in band_candidates.values():
        seen: set[str] = set()
        for _, b, _ in page_blocks:
            nm = normalize(_block_text(b))
            if nm:
                seen.add(nm)
        for nm in seen:
            norm_counts[nm] += 1

    page_width_by_page = {ex.pdf_page: ex.width for ex in extractions}
    for pdf_page in sorted(band_candidates.keys()):
        page_blocks = band_candidates[pdf_page]
        page_width = page_width_by_page[pdf_page]
        for idx, b, band in page_blocks:
            text = _block_text(b)
            nm = normalize(text)
            repetition_ratio = norm_counts[nm] / max(total_pages, 1) if nm else 0.0
            reason = "repetition" if repetition_ratio >= REPETITION_THRESHOLD else "regex"
            audit.append(
                StrippedBlock(
                    pdf_page=pdf_page,
                    band=band,
                    position=_block_position(b, page_width),
                    text=text.strip(),
                    reason=reason,
                    bbox=cast(
                        "tuple[float, float, float, float]",
                        tuple(round(float(v), 2) for v in b["bbox"]),
                    ),
                )
            )
            stripped[pdf_page].add(idx)

    audit.sort(key=lambda sb: (sb.pdf_page, sb.bbox[1], sb.bbox[0]))
    return audit, dict(stripped)


_SMALL_CAPS_SPACE_RE = re.compile(r"\b([A-Z])\s{1,3}([A-Z]{2,})")


def extract_page_section(top_band_blocks: list[dict], detected_folio: str | None) -> str | None:
    """Produce the `page_section` string for a page (D-08).

    Collapses small-caps spacing (``T ABLE`` → ``Table``) and strips the detected
    folio token. Returns None if there are no top-band blocks.
    """
    if not top_band_blocks:
        return None
    text = " ".join(
        span.get("text", "")
        for b in top_band_blocks
        for ln in b.get("lines", [])
        for span in ln.get("spans", [])
    )
    if detected_folio:
        text = re.sub(rf"\b{re.escape(detected_folio)}\b", "", text, flags=re.IGNORECASE)

    # Collapse small-caps spacing: "T ABLE" -> "Table"
    def _merge(m: re.Match) -> str:
        return (m.group(1) + m.group(2)).title()

    while True:
        new = _SMALL_CAPS_SPACE_RE.sub(_merge, text)
        if new == text:
            break
        text = new
    text = re.sub(r"\s+", " ", text).strip()
    return text or None
