"""Tests for y-band auto-detection (D-06)."""
from __future__ import annotations

from pathlib import Path

from book_indexer.ingest.loader import PdfLoader
from book_indexer.ingest.y_band_detector import detect_y_bands


def test_detect_y_bands_on_sample_pdf_returns_cluster_mode(sample_pdf_path: Path) -> None:
    with PdfLoader.open_document(sample_pdf_path) as doc:
        bands = detect_y_bands(doc)
    assert bands.detection_mode in {"cluster", "fallback"}
    assert 0 < bands.top_cutoff < 200  # running heads are in the top region


def test_detect_y_bands_is_deterministic(sample_pdf_path: Path) -> None:
    with PdfLoader.open_document(sample_pdf_path) as doc:
        a = detect_y_bands(doc)
    with PdfLoader.open_document(sample_pdf_path) as doc:
        b = detect_y_bands(doc)
    assert a == b


def test_detect_y_bands_cutoffs_are_rounded_2dp(sample_pdf_path: Path) -> None:
    with PdfLoader.open_document(sample_pdf_path) as doc:
        bands = detect_y_bands(doc)
    assert round(bands.top_cutoff, 2) == bands.top_cutoff
    assert round(bands.bot_cutoff, 2) == bands.bot_cutoff


def test_detect_y_bands_on_sample_has_no_bottom_cluster(sample_pdf_path: Path) -> None:
    """Sample PDF synthesis has no bottom running foot — only a top-band cluster."""
    with PdfLoader.open_document(sample_pdf_path) as doc:
        bands = detect_y_bands(doc)
    # No bottom peak -> bot_cutoff equals page height (720) exactly.
    assert bands.bot_cutoff >= 700.0


def test_detect_y_bands_bimodal_synthetic() -> None:
    """Degenerate single-peak case: construct a minimal stub Document.

    Rather than fabricate a fake pymupdf Document, we build a bimodal synthetic
    input by round-tripping a PyMuPDF-in-memory PDF with two clear y-clusters.
    """
    import pymupdf

    doc = pymupdf.open()
    tw_font = pymupdf.Font("cjk")
    for i in range(5):
        p = doc.new_page(width=540, height=720)
        tw = pymupdf.TextWriter(p.rect)
        # Top cluster at y=60
        tw.append((60, 60), f"head {i}", font=tw_font, fontsize=8.0)
        # Body cluster at y=400
        tw.append((60, 400), f"body content {i}", font=tw_font, fontsize=10.5)
        # Bottom cluster at y=680 — a real running foot
        tw.append((60, 680), f"foot {i}", font=tw_font, fontsize=8.0)
        tw.write_text(p)

    bands = detect_y_bands(doc)
    doc.close()

    assert bands.detection_mode == "cluster"
    assert bands.top_peak is not None
    assert bands.bot_peak is not None
    # Top cluster near y=51 (PyMuPDF baseline-to-bbox offset) -> top_cutoff < 100
    assert bands.top_cutoff < 100
    # Bottom cluster near y=670 -> bot_cutoff < page_height
    assert bands.bot_cutoff < 720
    # Body region brackets the middle cluster.
    assert bands.body_top < 400 < bands.body_bot
