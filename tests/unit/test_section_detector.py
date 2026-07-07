"""Unit tests for SectionDetector (Plan 01-03-b Task 3b.1).

Covers the full four-level hierarchy — Chapter (level 0), § N (level 1), N.NN
(level 2), N.NN.M (level 3) — and the defense-in-depth negative cases:
- body prose matching the regex but in plain CenturySchoolbook (rejected)
- TOC entries matching the regex but at 10.02pt plain (rejected)

We build PyMuPDF-shaped ``dict_output`` fixtures by hand to keep the tests
hermetic; integration against the real the reference corpus PDF lives in
``tests/integration/test_section_fixture_regression.py``.
"""
from __future__ import annotations

from book_indexer.ingest.section_detector import (
    CHAPTER_RE,
    CHAPTER_SEC_RE,
    SECTION_2_RE,
    SECTION_3_RE,
    SectionDetector,
    SectionStart,
)


def _span(text: str, font: str, size: float, flags: int = 20, y: float = 100.0,
          x: float = 72.0) -> dict:
    """Build a PyMuPDF-style span dict."""
    return {
        "text": text,
        "font": font,
        "size": size,
        "flags": flags,
        "bbox": (x, y, x + 200.0, y + 12.0),
    }


def _line(spans: list[dict], y: float = 100.0) -> dict:
    return {"bbox": (72.0, y, 540.0, y + 12.0), "spans": spans}


def _block(lines: list[dict]) -> dict:
    return {"type": 0, "bbox": (72.0, 50.0, 540.0, 700.0), "lines": lines}


def _page(blocks: list[dict]) -> dict:
    return {"width": 540.0, "height": 720.0, "blocks": blocks}


# ------------------------------------------------------------------
# Regex module-level constant tests
# ------------------------------------------------------------------


def test_regex_constants_exported() -> None:
    """Module exports the three regex constants as top-level names."""
    assert SECTION_2_RE.match("2.04 Why Object?")
    assert SECTION_3_RE.match("2.04.1 How to Object")
    assert CHAPTER_SEC_RE.match("§ 2")
    assert CHAPTER_SEC_RE.match("§ 2.")
    assert CHAPTER_RE.match("CHAPTER 1 PLANNING TO WIN")


def test_section_3_re_rejects_level_2() -> None:
    assert not SECTION_3_RE.match("2.04")


def test_section_2_re_rejects_chapter_sec() -> None:
    assert not SECTION_2_RE.match("§ 2")


# ------------------------------------------------------------------
# Detection positive cases
# ------------------------------------------------------------------


def test_level_3_detection_at_10_98_bold_italic() -> None:
    """A level-3 heading: BoldIt 10.98pt span whose text contains ``N.NN.M <title>``."""
    page = _page([
        _block([_line([
            _span("1.01.1 How to Be an Advocate", "CenturySchoolbook-BoldIt", 10.98,
                  flags=22, y=193.6),
        ])]),
    ])
    detector = SectionDetector()
    events = list(detector.detect_sections(page, pdf_page=43))
    assert len(events) == 1
    ev = events[0]
    assert ev.section_ref == "§1.01.1"
    assert ev.section_level == 3
    assert ev.title == "How to Be an Advocate"
    assert ev.pdf_page == 43
    assert ev.source == "body"


def test_level_2_detection_at_10_98_bold() -> None:
    """A level-2 heading: Bold 10.98pt, ref in one span, title in the next span on same line."""
    page = _page([
        _block([_line([
            _span("1.01", "CenturySchoolbook-Bold", 10.98, flags=20, y=102.1),
            _span("Why Be an Advocate?", "CenturySchoolbook-Bold", 10.98, flags=20, y=102.1,
                  x=200.0),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=43))
    assert len(events) == 1
    ev = events[0]
    assert ev.section_ref == "§1.01"
    assert ev.section_level == 2
    assert ev.title == "Why Be an Advocate?"


def test_level_1_detection_at_13_02_bold_body() -> None:
    """Chapter-start § N heading: Bold 13.02pt ``§ N.`` with title in a following span."""
    page = _page([
        _block([_line([
            _span("§ 1.", "CenturySchoolbook-Bold", 13.02, flags=20, y=208.5),
            _span(" ", "Arial-BoldMT", 13.02, flags=20, y=208.5, x=90.0),
            _span("ADVOCATING A CASE", "CenturySchoolbook-Bold", 13.02, flags=20, y=208.5,
                  x=110.0),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=42))
    assert len(events) == 1
    ev = events[0]
    assert ev.section_ref == "§1"
    assert ev.section_level == 1
    assert ev.title.upper() == "ADVOCATING A CASE"
    assert ev.source == "body"


def test_level_0_detection_at_18_pt_bold_caps() -> None:
    """A chapter heading: 18pt Bold drop-cap + 14.52pt caps spans assembling ``CHAPTER N <title>``."""
    # Emulate page 42's structure: "CHAPTER 1" line + "PLANNING TO WIN: EFFECTIVE PREPARATION" line.
    page = _page([
        _block([
            _line([
                _span("C", "CenturySchoolbook-Bold", 18.00, flags=20, y=85.3),
                _span("HAPTER", "CenturySchoolbook-Bold", 14.52, flags=20, y=88.7, x=90.0),
                _span(" ", "Arial-BoldMT", 14.52, flags=20, y=88.7, x=150.0),
                _span("1", "CenturySchoolbook-Bold", 18.00, flags=20, y=85.3, x=160.0),
            ], y=85.3),
            _line([
                _span("P", "CenturySchoolbook-Bold", 18.00, flags=20, y=129.8),
                _span("LANNING TO ", "CenturySchoolbook-Bold", 14.52, flags=20, y=133.2, x=90.0),
                _span("W", "CenturySchoolbook-Bold", 18.00, flags=20, y=129.8, x=200.0),
                _span("IN", "CenturySchoolbook-Bold", 14.52, flags=20, y=133.2, x=220.0),
                _span(":", "CenturySchoolbook-Bold", 18.00, flags=20, y=129.8, x=240.0),
                _span(" ", "CenturySchoolbook-Bold", 18.00, flags=20, y=129.8, x=250.0),
                _span("E", "CenturySchoolbook-Bold", 18.00, flags=20, y=129.8, x=260.0),
                _span("FFECTIVE ", "CenturySchoolbook-Bold", 14.52, flags=20, y=133.2, x=280.0),
            ], y=129.8),
            _line([
                _span("P", "CenturySchoolbook-Bold", 18.00, flags=20, y=149.8),
                _span("REPARATION", "CenturySchoolbook-Bold", 14.52, flags=20, y=153.2, x=90.0),
            ], y=149.8),
        ]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=42))
    chapters = [e for e in events if e.section_level == 0]
    assert len(chapters) == 1
    ev = chapters[0]
    assert ev.section_ref == "Chapter 1"
    assert "Planning" in ev.title
    assert "Win" in ev.title
    assert "Effective" in ev.title
    assert "Preparation" in ev.title


def test_level_3_priority_before_level_2() -> None:
    """A single ``1.01.1 Title`` span must emit level-3, not level-2.

    Because SECTION_2_RE (``^N.NN``) matches the prefix of ``N.NN.M``, the
    detector must check level-3 FIRST. This is the core priority-ordering
    correctness invariant (plan 01-03-b Task 3b.1, RESEARCH §SEC.2).
    """
    # The font is BoldIt which only fires the level-3 branch — proving that
    # priority IS font-gated, not regex-ordering, so a BoldIt text that *also*
    # looks like level-2 is not possible. The test below covers the
    # regex-ordering within the same font branch.
    page = _page([
        _block([_line([
            _span("2.04.3 Sub-section", "CenturySchoolbook-BoldIt", 10.98, flags=22, y=100.0),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=60))
    assert len(events) == 1
    assert events[0].section_level == 3
    assert events[0].section_ref == "§2.04.3"


# ------------------------------------------------------------------
# Negative cases (defense in depth)
# ------------------------------------------------------------------


def test_body_prose_with_section_ref_not_emitted() -> None:
    """A body paragraph saying ``See § 2.04 for details`` in plain
    CenturySchoolbook at 10.5pt must NOT emit a SectionStart. Font name
    filter is the primary defense per D-22."""
    page = _page([
        _block([_line([
            _span("See § 2.04 for details and § 2.04.1.",
                  "CenturySchoolbook", 10.5, flags=4, y=300.0),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=100))
    assert events == []


def test_toc_entry_not_emitted() -> None:
    """A TOC row at plain CenturySchoolbook 10.02pt with dot-leaders must NOT
    emit. Even without page-range scoping, the font filter disqualifies."""
    page = _page([
        _block([_line([
            _span("1.01 Why Be an Advocate? ........................... 2",
                  "CenturySchoolbook", 10.02, flags=4, y=173.6),
        ])]),
        _block([_line([
            _span("1.01.1 How to Be an Advocate .......................... 2",
                  "CenturySchoolbook", 10.02, flags=4, y=221.7),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=14))
    assert events == []


def test_bold_span_without_matching_regex_not_emitted() -> None:
    """A bold 10.98pt heading that is NOT a section number (e.g., a sub-head
    like ``A. SCOPE``) must NOT emit. Regex is the second gate."""
    page = _page([
        _block([_line([
            _span("A. SCOPE", "CenturySchoolbook-Bold", 12.0, flags=20, y=233.8),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=42))
    assert events == []


def test_running_head_10_50pt_not_emitted_as_level_1_start() -> None:
    """The 10.50pt ``§ N`` in the running head is a marker for active-chapter
    tracking, not a new section-level-1 boundary. It must NOT emit a
    SectionStart. (Level-1 starts are detected at 13.02pt in body signature.)"""
    page = _page([
        _block([_line([
            _span("§ 1", "CenturySchoolbook-Bold", 10.50, flags=20, y=55.8),
            _span(" ", "CenturySchoolbook-Bold", 10.50, flags=20, y=55.8),
            _span("A", "CenturySchoolbook-Bold", 10.50, flags=20, y=55.8),
            _span("DVOCATING A", "CenturySchoolbook-Bold", 8.52, flags=20, y=57.8),
        ])]),
    ])
    events = list(SectionDetector().detect_sections(page, pdf_page=50))
    assert events == []


# ------------------------------------------------------------------
# Detector is stateless / deterministic
# ------------------------------------------------------------------


def test_detector_is_deterministic_across_calls() -> None:
    """Two calls on identical input produce identical events."""
    page = _page([
        _block([_line([
            _span("1.01", "CenturySchoolbook-Bold", 10.98, flags=20, y=102.1),
            _span("Why Be an Advocate?", "CenturySchoolbook-Bold", 10.98, flags=20, y=102.1,
                  x=200.0),
        ])]),
        _block([_line([
            _span("1.01.1 How to Be an Advocate",
                  "CenturySchoolbook-BoldIt", 10.98, flags=22, y=193.6),
        ])]),
    ])
    det = SectionDetector()
    a = list(det.detect_sections(page, pdf_page=43))
    b = list(det.detect_sections(page, pdf_page=43))
    assert a == b
    assert [e.section_ref for e in a] == ["§1.01", "§1.01.1"]


def test_dataclass_is_frozen() -> None:
    """SectionStart is frozen — caller cannot mutate an event mid-pipeline."""
    ev = SectionStart(section_ref="§1.01", section_level=2, pdf_page=43,
                      token_offset=0, title="Why Be an Advocate?", source="body")
    import dataclasses
    assert dataclasses.is_dataclass(ev)
    try:
        ev.section_ref = "§1.02"  # type: ignore[misc]
    except (AttributeError, dataclasses.FrozenInstanceError):
        pass
    else:
        raise AssertionError("SectionStart should be frozen")
