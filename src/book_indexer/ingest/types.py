"""Shared dataclasses for the ingest layer.

These types form the stable contract between extractor, y-band detector,
header/footer stripper, classifier, folio resolver, and the eventual corpus writer.
All frozen to prevent accidental mutation during downstream passes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PageExtraction:
    """Output of extract_page(): raw PyMuPDF dict + page metadata."""
    pdf_page: int              # 0-based
    width: float
    height: float
    dict_output: dict[str, Any]   # PyMuPDF get_text("dict") output


@dataclass(frozen=True)
class YBands:
    """Auto-detected y-band cutoffs (D-06)."""
    top_cutoff: float          # y < top_cutoff => strip band (running head)
    bot_cutoff: float          # y > bot_cutoff => strip band (running foot); equals page height if no bottom band
    body_top: float            # body region top
    body_bot: float            # body region bottom
    detection_mode: str        # "cluster" | "fallback"
    top_peak: float | None     # center of top cluster
    bot_peak: float | None     # center of bottom cluster (None if no bottom)
    warning: str | None        # non-empty if fell back


@dataclass(frozen=True)
class StrippedBlock:
    """One audit row per stripped header/footer block (D-09)."""
    pdf_page: int
    band: str                  # "top" | "bottom"
    position: str              # "left" | "center" | "right"
    text: str
    reason: str                # "regex" | "repetition" | "chapter_title"
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class BlockClassification:
    """Output of classify_blocks() — one per block on a page."""
    pdf_page: int
    block_index: int
    block_type: str            # "body" | "footnote" | "header_footer" | "image"
    block_role: str | None     # "running_head" | "running_foot" | "chapter_title" | "footnote" | None
    avg_font_size: float | None
    y_center: float | None
    bbox: tuple[float, float, float, float]
    ambiguity_reason: str | None  # non-None => logged to classification_audit
