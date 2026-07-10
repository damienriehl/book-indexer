"""Page-level PyMuPDF extraction.

Per RESEARCH.md §A.2: we build our own reading order and rely on PyMuPDF's
default extraction flags — the optional PyMuPDF re-sort heuristic merges lines
aggressively on the reference corpus, which breaks block granularity.
Per RESEARCH.md §A.6: determinism confirmed on pymupdf 1.27.2.2 + identical inputs.
Per plan 01-03-b: every span exposes font_name + flags + bbox verbatim from
PyMuPDF — section detector gates headings on font name (CenturySchoolbook-Bold
vs -BoldIt vs plain).
"""
from __future__ import annotations

from typing import Any, cast

import pymupdf

from .types import PageExtraction


def extract_page(doc: pymupdf.Document, pdf_page: int) -> PageExtraction:
    """Single-page extraction. Caller holds the Document open."""
    page = doc[pdf_page]
    # Default flags only; never re-sort at extraction time — we build our own order.
    d = cast("dict[str, Any]", page.get_text("dict"))
    return PageExtraction(
        pdf_page=pdf_page,
        width=float(d["width"]),
        height=float(d["height"]),
        dict_output=d,
    )


def extract_all(doc: pymupdf.Document) -> list[PageExtraction]:
    """Extract every page of an open Document in ascending pdf_page order."""
    return [extract_page(doc, i) for i in range(doc.page_count)]


def block_reading_order(page_dict: dict) -> list[dict]:
    """Return text blocks (type==0) sorted by (round(y0,2), round(x0,2)).

    Per RESEARCH.md §A.2: rounding defuses float-jitter; our own sort is more
    deterministic than PyMuPDF's built-in re-sort heuristic.
    """
    blocks = [b for b in page_dict["blocks"] if b["type"] == 0]
    blocks.sort(key=lambda b: (round(b["bbox"][1], 2), round(b["bbox"][0], 2)))
    return blocks
