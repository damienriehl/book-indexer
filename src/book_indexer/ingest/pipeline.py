"""End-to-end ingest pipeline composition (plan 01-04 Task 4.2).

Sequence:
  1. Open PDF (loader)
  2. Extract every page (extractor, PyMuPDF ``get_text("dict")``)
  3. Detect y-bands (y_band_detector)
  4. Strip headers / footers → audit rows + per-page stripped indices
  5. Compute body-font mode (classifier)
  6. Classify blocks per page (classifier)
  7. Resolve folios via the 4-tier cascade (folio_resolver)
  8. Compute ``page_section`` (chapter-title running head text, D-08)
     and ``active_chapter_section`` (``§ N``, D-08) per page
  9. Detect section headings (section_detector), resolve bounds
     (section_resolver), build the section tree (section_tree)
 10. Load spaCy tokenizer
 11. Tokenize body + footnote blocks (skip header_footer/image)
 12. Write the deterministic SQLite corpus (corpus_writer)
 13. VACUUM + ANALYZE + close

The pipeline is intentionally synchronous and single-process — PyMuPDF is
fast enough at 259 pages and threading introduces nondeterminism. Runtime
for the 10-page sample on a laptop: ~2-4 seconds dominated by the one-time
spaCy model load.
"""
from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

import pymupdf
import spacy

from . import corpus_writer
from .classifier import classify_blocks, compute_body_font_mode
from .corpus_writer import MetadataInputs
from .extractor import extract_all
from .folio_resolver import CascadeFolioResolver, FolioInfo
from .header_footer import (
    extract_active_chapter_section,
    extract_page_section,
    strip_headers_footers,
)
from .loader import PdfLoader
from .section_detector import SectionDetector, SectionStart
from .section_resolver import SectionResolver
from .section_tree import build_section_tree
from .tokenizer import (
    TokenRecord,
    load_tokenizer,
    spacy_model_sha256,
    tokenize_block,
)
from .types import PageExtraction, YBands
from .y_band_detector import detect_y_bands

# Default PyMuPDF text-extraction flag value (RESEARCH.md §A.6). We use the
# defaults — not passing ``flags=`` to ``get_text("dict")``. This constant is
# persisted in ``extraction_metadata.pymupdf_textflags`` for provenance.
PYMUPDF_TEXTFLAGS_DICT = 199

# Pipeline version string — bumped when the end-to-end behavior changes in a
# way that invalidates an existing corpus (e.g., new token-level override, new
# section-level CHECK constraint). Stored in ``extraction_metadata``.
PIPELINE_VERSION = corpus_writer.PIPELINE_VERSION


def _collect_top_band_blocks(ex: PageExtraction, y_bands: YBands) -> list[dict]:
    return [
        b
        for b in ex.dict_output.get("blocks", [])
        if b.get("type") == 0 and float(b["bbox"][1]) < y_bands.top_cutoff
    ]


def _build_section_starts(
    extractions: list[PageExtraction],
    stripped_indices: dict[int, set[int]],
    active_chapter_sections: dict[int, str | None],
) -> list[SectionStart]:
    """Run the section detector over every page, skipping already-stripped
    top-band blocks so running-head ``§ N`` tokens don't double-fire level-1
    detections.
    """
    detector = SectionDetector()
    events: list[SectionStart] = []
    # Track the last-seen level-1 / level-0 ref for active_chapter bookkeeping.
    active_chapter = None
    for ex in extractions:
        # Build a page_dict whose blocks exclude stripped running-head indices
        # so the detector's linear scan doesn't see them. The detector gates on
        # font-name anyway, but this matches the contract that blocks stripped
        # at the header pass never reach the section detector.
        stripped = stripped_indices.get(ex.pdf_page, set())
        if stripped:
            filtered_blocks = [
                b for i, b in enumerate(ex.dict_output.get("blocks", []))
                if i not in stripped
            ]
            page_dict = {**ex.dict_output, "blocks": filtered_blocks}
        else:
            page_dict = ex.dict_output

        for start in detector.detect_sections(
            page_dict,
            ex.pdf_page,
            active_chapter_section=active_chapter_sections.get(ex.pdf_page),
            active_chapter=active_chapter,
        ):
            events.append(start)
            if start.section_level == 0:
                active_chapter = start.section_ref
    return events


def _build_token_records(
    nlp,
    extractions: list[PageExtraction],
    classifications_by_page: dict[int, list],
    stripped_indices: dict[int, set[int]],
) -> list[TokenRecord]:
    """Run the tokenizer over every body + footnote block, page-by-page.

    ``token_index`` is a running per-page counter, preserving the exact
    ordering the section detector and boundary resolver used for their
    ``start_token_offset`` / ``end_token_offset`` fields. If this ordering
    diverges from the detector's, section_id assignment silently miscounts —
    so both passes walk blocks in ``block_index`` order.
    """
    tokens: list[TokenRecord] = []
    for ex in extractions:
        per_page_idx = 0
        classifications = classifications_by_page.get(ex.pdf_page, [])
        stripped = stripped_indices.get(ex.pdf_page, set())
        # Sort by block_index (extractor's reading order determines block_index
        # when classify_blocks is called on the same ``dict_output``).
        for c in sorted(classifications, key=lambda x: x.block_index):
            if c.block_index in stripped:
                continue
            if c.block_type not in ("body", "footnote"):
                continue
            block = ex.dict_output["blocks"][c.block_index]
            recs = tokenize_block(nlp, ex.pdf_page, block, c, per_page_idx)
            tokens.extend(recs)
            per_page_idx += len(recs)
    return tokens


def run_ingest(pdf_path: str | Path, out_dir: str | Path) -> Path:
    """End-to-end Phase-1 pipeline; returns the path of the written SQLite file.

    ``out_dir`` is created if missing. The resulting file lands at
    ``out_dir / 'page_corpus.sqlite'``. Any pre-existing file at that path is
    overwritten atomically (the corpus_writer opens with ``unlink()`` first).
    """
    # Determinism env (belt + suspenders — pytest conftest sets these too).
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("TZ", "UTC")

    pdf_path = Path(pdf_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = out_dir / "page_corpus.sqlite"

    nlp = load_tokenizer(Path("config/legal_lemma_overrides.yaml"))

    with PdfLoader.open_document(pdf_path) as doc:
        extractions = extract_all(doc)
        y_bands = detect_y_bands(doc)
        hf_audit, stripped_idx = strip_headers_footers(extractions, y_bands)
        body_mode = compute_body_font_mode(extractions, y_bands)

        # Classify every page's blocks; stash a per-page list for the
        # tokenizer + audit writer.
        all_classifications: list = []
        classifications_by_page: dict[int, list] = defaultdict(list)
        for ex in extractions:
            page_class = classify_blocks(ex.pdf_page, ex.dict_output, y_bands, body_mode)
            all_classifications.extend(page_class)
            for c in page_class:
                classifications_by_page[c.pdf_page].append(c)

        # Resolve folios (4-tier cascade). CascadeFolioResolver eagerly
        # resolves + monotonicity-checks in its constructor.
        resolver = CascadeFolioResolver(doc, extractions, y_bands)
        folio_infos: dict[int, FolioInfo] = resolver.resolve_all()
        folio_audit = resolver.audit_rows()

        # Per-page section metadata.
        active_chapter_sections = extract_active_chapter_section(extractions, y_bands)
        page_sections: dict[int, str | None] = {}
        for ex in extractions:
            top_band_blocks = _collect_top_band_blocks(ex, y_bands)
            folio = folio_infos[ex.pdf_page].folio
            page_sections[ex.pdf_page] = extract_page_section(top_band_blocks, folio)

        # Detect section headings and resolve their intervals. The boundary
        # resolver needs a book-end marker — we use (last_pdf_page, a large
        # sentinel token_offset) so "end of book" sections close cleanly.
        section_starts = _build_section_starts(
            extractions, stripped_idx, active_chapter_sections
        )
        # book_end_marker: one past the last page so even the last section's
        # closed interval terminates at an unreachable bound.
        last_page = max((ex.pdf_page for ex in extractions), default=0)
        book_end_marker = (last_page, 10**9)
        resolved_sections = SectionResolver().resolve(section_starts, book_end_marker)
        tree_sections = build_section_tree(resolved_sections)

        # Block counts + page bboxes.
        block_counts: dict[int, int] = {}
        bbox_pages: dict[int, tuple[float, float, float, float]] = {}
        for ex in extractions:
            block_counts[ex.pdf_page] = sum(
                1 for b in ex.dict_output.get("blocks", []) if b.get("type") == 0
            )
            bbox_pages[ex.pdf_page] = (0.0, 0.0, ex.width, ex.height)

        # Tokenize body + footnote blocks.
        tokens = _build_token_records(
            nlp, extractions, classifications_by_page, stripped_idx
        )

        # Section start_folio lookup (from pages.folio).
        start_folios = {
            s.global_id: (folio_infos[s.start_pdf_page].folio or "")
            for s in tree_sections
        }

        # ---------------- Write corpus ----------------
        conn = corpus_writer.open_deterministic(corpus_path)
        try:
            corpus_writer.create_schema(conn)
            corpus_writer.write_pages(
                conn, folio_infos, page_sections, active_chapter_sections,
                block_counts, bbox_pages,
            )
            corpus_writer.write_sections(conn, tree_sections, start_folios)
            corpus_writer.write_tokens(conn, tokens)
            corpus_writer.assign_section_ids(conn)
            corpus_writer.write_folio_audit(conn, folio_audit)
            corpus_writer.write_header_footer_audit(conn, hf_audit)
            corpus_writer.write_classification_audit(
                conn, all_classifications, body_mode, y_bands.body_top, y_bands.body_bot,
            )
            corpus_writer.write_metadata(
                conn,
                MetadataInputs(
                    pdf_path=pdf_path,
                    pdf_page_count=doc.page_count,
                    pymupdf_version=pymupdf.__version__,
                    pymupdf_textflags=PYMUPDF_TEXTFLAGS_DICT,
                    spacy_version=spacy.__version__,
                    spacy_model="en_core_web_lg",
                    spacy_model_sha256=spacy_model_sha256(),
                    y_bands=y_bands,
                    body_font_mode=body_mode,
                    footnote_threshold=body_mode - 1.0,
                    section_fixture_path=Path("fixtures/sections.yaml"),
                    folio_fixture_path=Path("fixtures/folios.yaml"),
                    pipeline_version=PIPELINE_VERSION,
                ),
            )
        finally:
            corpus_writer.finalize(conn)

    return corpus_path
