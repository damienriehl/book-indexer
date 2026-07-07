#!/usr/bin/env python3
"""Regenerate fixtures/sample_10pages.pdf — a deterministic 10-page synthetic
PDF for fast unit tests. Exercises the full normalization contract:
  - Roman + Arabic folios (top-right / top-left)
  - Ligatures (U+FB01 ﬁ, U+FB02 ﬂ, U+FB03 ﬃ)
  - Smart quotes (U+2018/19/1C/1D), en-dash (U+2013), em-dash (U+2014)
  - Soft hyphens (U+00AD) and NBSP (U+00A0), ZWSP (U+200B)
  - Body font-size (10.5pt) vs footnote font-size (8.52pt) split
  - Line-end hyphen with concatenated token OOV (`crossexamination` — do NOT rejoin)
  - Cross-page hyphenation with in-vocab token (`evidence` — DO rejoin) — D-16
  - Blank page (monotonicity skip)
  - Section heading hints (a § N running head at 10.50pt Bold, and major/sub
    section lines using larger sizes on page 4)

Fonts: PyMuPDF's built-in ``cjk`` font (a serif, Unicode-capable font shipped
inside the PyMuPDF wheel) is used for *every* text insertion. The stdlib-14 PDF
base fonts (Helvetica, Times-Roman) use WinAnsi encoding which silently drops
non-ASCII code points like U+FB01 ligatures — unusable for the normalization
fixtures. The ``cjk`` font round-trips Latin text and all of the Unicode code
points we need. Tests assert on font *size* and *text content* (never font name),
so this substitution is safe.

Run:
    PYTHONHASHSEED=0 TZ=UTC uv run python fixtures/build_sample_pdf.py
"""
from __future__ import annotations

from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "sample_10pages.pdf"
PAGE_W, PAGE_H = 540.0, 720.0

# Single Unicode-capable font for everything. `cjk` is shipped inside PyMuPDF
# and is deterministic across platforms (no system-font dependency).
_UNI_FONT = pymupdf.Font("cjk")


def _write(page: pymupdf.Page, text: str, x: float, y: float, fontsize: float) -> None:
    """Write `text` at (x, y) in `fontsize` using the Unicode font via TextWriter."""
    tw = pymupdf.TextWriter(page.rect)
    tw.append((x, y), text, font=_UNI_FONT, fontsize=fontsize)
    tw.write_text(page)


def build() -> None:
    doc = pymupdf.open()

    # --- Page 0 — Title page (no folio) ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "SAMPLE FIXTURE", PAGE_W / 2 - 80, PAGE_H / 2, 16.0)

    # --- Page 1 — Roman folio i (top-right) ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "i", PAGE_W - 80, 60, 7.98)
    _write(p, "Preface. The quick brown fox jumps over the lazy dog.", 60, 140, 10.5)

    # --- Page 2 — Roman folio ii (top-left) + ligatures ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "ii", 60, 60, 7.98)
    _write(
        p,
        "Chapter heading running head test. The ﬁrst ﬂame was ﬃcial.",
        60, 140, 10.5,
    )

    # --- Page 3 — Roman folio iii + smart quotes + dashes ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "iii", PAGE_W - 80, 60, 7.98)
    _write(
        p,
        "Smart quotes: ‘single’ and “double” —"
        " en-dash – em-dash —.",
        60, 140, 10.5,
    )

    # --- Page 4 — Arabic folio 1 (top-right) + section heading + footnote ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "§ 1", PAGE_W - 80, 55.8, 10.50)       # chapter running head
    _write(p, "1", PAGE_W - 60, 70, 7.98)                  # Arabic folio
    _write(p, "1.01 Why Be an Advocate?", 60, 120, 10.98)   # major section
    _write(p, "1.01.1 How to Be an Advocate", 60, 150, 10.98)  # sub-section
    _write(p, "Body paragraph about procedure.", 60, 200, 10.5)
    _write(p, "1. See res judicata doctrine.", 60, 660, 8.52)  # footnote

    # --- Page 5 — Arabic folio 2 (top-left) + mid-line hyphen, OOV rejoin ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "2", 60, 70, 7.98)
    # Line-end hyphen; concatenation would be "crossexamination" (OOV) → keep hyphen.
    _write(p, "This is cross-", 60, 140, 10.5)
    _write(p, "examination evidence.", 60, 156, 10.5)

    # --- Page 6 — Arabic folio 3 + cross-page hyphen start ("evi-") ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "3", PAGE_W - 80, 70, 7.98)
    _write(p, "The record shows that the evi-", 60, 700, 10.5)

    # --- Page 7 — Arabic folio 4 (top-left) + continuation "dence" + soft hyphen ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "4", 60, 70, 7.98)
    _write(p, "dence was compelling.", 60, 140, 10.5)
    # Soft hyphen U+00AD inside a word (should be stripped in norm/lemma).
    _write(p, "Also contains soft­hyphen here.", 60, 170, 10.5)

    # --- Page 8 — Blank page (no folio, no spans). ---
    _ = doc.new_page(width=PAGE_W, height=PAGE_H)

    # --- Page 9 — Arabic folio 5 (top-right) + NBSP / ZWSP ---
    p = doc.new_page(width=PAGE_W, height=PAGE_H)
    _write(p, "5", PAGE_W - 80, 70, 7.98)
    _write(
        p,
        "Final page with NBSP here and ZWSP​here.",
        60, 140, 10.5,
    )

    doc.save(str(OUT), deflate=True, garbage=4, clean=True)
    doc.close()


if __name__ == "__main__":
    build()
    print(f"wrote {OUT}")
