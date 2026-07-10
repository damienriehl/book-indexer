"""Y-band auto-detection (D-06).

Clusters block y-positions across the full document to derive running-head/foot
strip cutoffs empirically. For the reference corpus specifically, the top cluster at y~55-78
exists; the bottom cluster is effectively absent (real running-foot text has no
systematic repetition) — but whitespace-only blocks sometimes land at a fixed
bottom y-coordinate, so the detector MUST exclude empty blocks from the histogram
before selecting peaks. See Rule 1 deviation note in SUMMARY.

Per RESEARCH §C.1:
  - 1-pt histogram bins
  - top peak: most common y_top within y < h * 0.15
  - bottom peak: most common y_top within y > h * 0.85 (may be absent)
  - cutoff = peak +/- PEAK_BAND_PAD_PT
  - fallback = hardcoded 10%/10% bands when neither peak is credible

Peak-strength guard (empirically tuned):
  - Top/bottom peak must appear on >= PEAK_PAGE_FRACTION (20%) of pages to count
    as a running-head/foot cluster. Below that threshold we suspect the peak is
    noise (stray body blocks near the margin) and refuse to move the cutoff.
"""
from __future__ import annotations

import statistics
from collections import Counter
from typing import Any, cast

import pymupdf

from .types import YBands

TOP_REGION_FRACTION = 0.15      # "top" means y in first 15% of page height
BOT_REGION_FRACTION = 0.85      # "bottom" means y in last 15% of page height
PEAK_BAND_PAD_PT = 10.0         # cutoff = peak y +/- this many points
PEAK_PAGE_FRACTION = 0.20       # peak must occur on >= 20% of pages to count
FALLBACK_TOP_FRAC = 0.10        # if clustering weak, strip top 10%
FALLBACK_BOT_FRAC = 0.10        # and bottom 10%


def _block_is_empty(block: dict) -> bool:
    """True if the block has no non-whitespace span text.

    Whitespace-only blocks appear at stable y-coordinates in some PDFs
    (the reference corpus has 250 such blocks at y=671.5) and would otherwise trip the
    bottom-band detector into thinking there's a running foot.
    """
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if (span.get("text") or "").strip():
                return False
    return True


def detect_y_bands(doc: pymupdf.Document) -> YBands:
    """Auto-detect top/bottom strip bands from cross-page block-top distribution.

    D-06 specifies clustering; D-08 notes that the reference corpus has top-only (bimodal
    but one peak is empty). Algorithm returns `detection_mode="cluster"` when
    at least one peak is credible; returns `detection_mode="fallback"` only
    when neither side has a credible peak.
    """
    page_heights: list[float] = []
    block_tops: list[float] = []

    for pdf_page in range(doc.page_count):
        page = doc[pdf_page]
        page_heights.append(float(page.rect.height))
        d = cast("dict[str, Any]", page.get_text("dict"))
        for b in d["blocks"]:
            if b["type"] != 0:
                continue
            # Exclude whitespace-only blocks — extraction artifacts with no visible text.
            if _block_is_empty(b):
                continue
            block_tops.append(float(b["bbox"][1]))

    if not page_heights:
        raise ValueError("Document has no pages")
    h = statistics.median(page_heights)
    page_count = len(page_heights)
    min_peak_count = max(1, int(round(PEAK_PAGE_FRACTION * page_count)))

    # No blocks at all -> fallback
    if not block_tops:
        return YBands(
            top_cutoff=round(h * FALLBACK_TOP_FRAC, 2),
            bot_cutoff=round(h * (1.0 - FALLBACK_BOT_FRAC), 2),
            body_top=round(h * FALLBACK_TOP_FRAC, 2),
            body_bot=round(h * (1.0 - FALLBACK_BOT_FRAC), 2),
            detection_mode="fallback",
            top_peak=None,
            bot_peak=None,
            warning="No text blocks found; using hardcoded bands",
        )

    # Histogram with 1pt bins
    hist: Counter[int] = Counter(int(round(y)) for y in block_tops)

    top_region_cutoff = h * TOP_REGION_FRACTION
    bot_region_cutoff = h * BOT_REGION_FRACTION

    top_bins = {y: c for y, c in hist.items() if y < top_region_cutoff}
    bot_bins = {y: c for y, c in hist.items() if y > bot_region_cutoff}

    top_peak: float | None = None
    bot_peak: float | None = None
    warnings: list[str] = []

    if top_bins:
        # Deterministic tie-break: highest count; on tie, smaller y wins.
        top_y, top_count = max(top_bins.items(), key=lambda kv: (kv[1], -kv[0]))
        if top_count >= min_peak_count:
            top_peak = float(top_y)
        else:
            warnings.append(f"top peak weak: {top_count} blocks (need {min_peak_count})")

    if bot_bins:
        # Deterministic tie-break: highest count; on tie, larger y wins.
        bot_y, bot_count = max(bot_bins.items(), key=lambda kv: (kv[1], kv[0]))
        if bot_count >= min_peak_count:
            bot_peak = float(bot_y)

    if top_peak is None and bot_peak is None:
        return YBands(
            top_cutoff=round(h * FALLBACK_TOP_FRAC, 2),
            bot_cutoff=round(h * (1.0 - FALLBACK_BOT_FRAC), 2),
            body_top=round(h * FALLBACK_TOP_FRAC, 2),
            body_bot=round(h * (1.0 - FALLBACK_BOT_FRAC), 2),
            detection_mode="fallback",
            top_peak=None,
            bot_peak=None,
            warning="; ".join(warnings) or "No strong peaks detected",
        )

    top_cutoff = (top_peak + PEAK_BAND_PAD_PT) if top_peak is not None else (h * FALLBACK_TOP_FRAC)
    # No bottom peak -> bot_cutoff at page height (effectively no bottom strip).
    bot_cutoff = (bot_peak - PEAK_BAND_PAD_PT) if bot_peak is not None else h

    return YBands(
        top_cutoff=round(top_cutoff, 2),
        bot_cutoff=round(bot_cutoff, 2),
        body_top=round(top_cutoff, 2),
        body_bot=round(bot_cutoff, 2),
        detection_mode="cluster",
        top_peak=top_peak,
        bot_peak=bot_peak,
        warning="; ".join(warnings) if warnings else None,
    )
