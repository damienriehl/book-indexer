"""Block classification: body / footnote / header_footer / image (D-10..D-13).

Signal (D-10): a block is a footnote iff BOTH (a) avg font-size is ≥1pt below
the body-font mode AND (b) the block's y_center falls in the bottom quartile
of the body region. Signals disagree → default body with `ambiguity_reason`
logged (D-11). Sidebars / block-quotes → body (D-12). Superscript spans
(flag bit 0) are excluded from avg-size computation.
"""
from __future__ import annotations

import statistics
from collections import Counter
from collections.abc import Sequence
from typing import cast

from .types import BlockClassification, PageExtraction, YBands

BODY_MODE_TOLERANCE = 0.5        # D-13: body is mode +/- 0.5pt
FOOTNOTE_DELTA_PT = 1.0          # D-10: footnote font is >=1pt below body mode
SUPERSCRIPT_FLAG_BIT = 1         # span flags bit 0 (value 1) => superscript


def compute_body_font_mode(
    extractions: Sequence[PageExtraction],
    y_bands: YBands,
) -> float:
    """Mode of non-strip-band span font sizes (D-13).

    Superscripts are excluded (bit 0 of span flags). Ties are broken by
    preferring the smaller size to be conservative about the footnote band.
    """
    sizes: list[float] = []
    for ex in extractions:
        for b in ex.dict_output["blocks"]:
            if b["type"] != 0:
                continue
            y0 = float(b["bbox"][1])
            y1 = float(b["bbox"][3])
            if y0 < y_bands.top_cutoff or y1 > y_bands.bot_cutoff:
                continue  # skip strip bands
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("flags", 0) & SUPERSCRIPT_FLAG_BIT:
                        continue
                    sizes.append(round(float(span["size"]), 2))
    if not sizes:
        raise ValueError("No non-strip-band spans found; cannot compute body font mode")
    counts = Counter(sizes)
    top_count = max(counts.values())
    winners = [s for s, c in counts.items() if c == top_count]
    return float(min(winners))


def _avg_non_superscript_size(block: dict) -> float | None:
    sizes: list[float] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if span.get("flags", 0) & SUPERSCRIPT_FLAG_BIT:
                continue
            sizes.append(float(span["size"]))
    if not sizes:
        return None
    return statistics.mean(sizes)


def classify_blocks(
    pdf_page: int,
    page_dict: dict,
    y_bands: YBands,
    body_font_mode: float,
) -> list[BlockClassification]:
    """Produce one classification per block on the page.

    Logic (D-10, D-11, D-12):
      - image block (type != 0) -> block_type="image"
      - block in top strip band -> "header_footer", role="running_head"
      - block in bottom strip band -> "header_footer", role="running_foot"
      - else if (avg_size <= body_mode - 1.0) AND (y_center in bottom quartile)
                -> "footnote"
      - else if exactly one signal fires -> "body" with ambiguity_reason
      - else -> "body"
    """
    results: list[BlockClassification] = []
    body_top = y_bands.body_top
    body_bot = y_bands.body_bot
    bottom_quartile_threshold = body_top + 0.75 * (body_bot - body_top)

    for block_index, block in enumerate(page_dict["blocks"]):
        bbox = cast(
            "tuple[float, float, float, float]",
            tuple(round(float(v), 2) for v in block["bbox"]),
        )
        y0, y1 = bbox[1], bbox[3]

        if block["type"] != 0:
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="image", block_role="media",
                    avg_font_size=None, y_center=(y0 + y1) / 2.0, bbox=bbox,
                    ambiguity_reason=None,
                )
            )
            continue

        if y0 < y_bands.top_cutoff:
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="header_footer", block_role="running_head",
                    avg_font_size=_avg_non_superscript_size(block),
                    y_center=(y0 + y1) / 2.0, bbox=bbox, ambiguity_reason=None,
                )
            )
            continue

        if y1 > y_bands.bot_cutoff:
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="header_footer", block_role="running_foot",
                    avg_font_size=_avg_non_superscript_size(block),
                    y_center=(y0 + y1) / 2.0, bbox=bbox, ambiguity_reason=None,
                )
            )
            continue

        avg = _avg_non_superscript_size(block)
        if avg is None:
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="body", block_role=None,
                    avg_font_size=None, y_center=(y0 + y1) / 2.0, bbox=bbox,
                    ambiguity_reason="empty_spans",
                )
            )
            continue

        y_center = (y0 + y1) / 2.0
        size_small = avg <= (body_font_mode - FOOTNOTE_DELTA_PT)
        in_bottom_quartile = y_center >= bottom_quartile_threshold

        if size_small and in_bottom_quartile:
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="footnote", block_role="footnote",
                    avg_font_size=round(avg, 2), y_center=round(y_center, 2),
                    bbox=bbox, ambiguity_reason=None,
                )
            )
        elif size_small != in_bottom_quartile:
            reason = (
                f"size={avg:.2f} body_mode={body_font_mode:.2f} "
                f"y_center={y_center:.2f} threshold={bottom_quartile_threshold:.2f}"
            )
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="body", block_role=None,
                    avg_font_size=round(avg, 2), y_center=round(y_center, 2),
                    bbox=bbox, ambiguity_reason=reason,
                )
            )
        else:
            results.append(
                BlockClassification(
                    pdf_page=pdf_page, block_index=block_index,
                    block_type="body", block_role=None,
                    avg_font_size=round(avg, 2), y_center=round(y_center, 2),
                    bbox=bbox, ambiguity_reason=None,
                )
            )
    return results
