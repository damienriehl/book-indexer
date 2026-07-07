"""4-tier folio cascade (D-01..D-05, FOL-01..FOL-05).

Tier 1: PDF /PageLabels via pymupdf.Document.get_page_labels()
Tier 2: Six-margin regex scan (TL/TC/TR/BL/BC/BR) with verso/recto parity
Tier 3: Contiguity inference (fills blanks, chapter-start suppression)
Tier 4: Offset from nearest confident anchor
Plus:   check_monotonicity() build-time assertion (D-05, FOL-05)

Day-1 spike (RESEARCH.md §Day-1 Spike Resolved): get_page_labels() on the reference corpus
returns [] — Tier-1 is a dead code path for this book. Kept for companion-volume
reusability (*Pretrial Litigation*, *Trial Advocacy* may differ).
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

import pymupdf

from .errors import FolioContiguityError, FolioMonotonicityError
from .types import PageExtraction, YBands

# D-02 Tier-2 regex: single alternation covers Arabic and Roman (case-insensitive).
FOLIO_RE = re.compile(r"^\s*(\d+|[ivxlcdm]+)\s*$", re.IGNORECASE)
PREFIX_RE = re.compile(r"^\s*([A-Za-z])-(\d+)\s*$")  # e.g., "A-1", "B-3"

_ROMAN_TABLE = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _is_roman(s: str) -> bool:
    return bool(s) and all(ch.lower() in _ROMAN_TABLE for ch in s)


def _roman_to_int(s: str) -> int:
    total, prev = 0, 0
    for ch in s.lower()[::-1]:
        val = _ROMAN_TABLE[ch]
        total += -val if val < prev else val
        prev = val
    return total


def parse_folio(raw: str) -> tuple[str, str, int]:
    """Return (folio_str, folio_style, int_value).

    folio_str is the stripped input; folio_style is "arabic" / "roman" / "prefix";
    int_value is the integer rank (for Roman, the Roman-to-int conversion; for
    prefix like "A-3", the trailing integer).

    Raises ValueError if raw is not a valid folio.
    """
    stripped = raw.strip()
    m = FOLIO_RE.match(stripped)
    if m:
        tok = m.group(1)
        if tok.isdigit():
            return stripped, "arabic", int(tok)
        if _is_roman(tok):
            return stripped, "roman", _roman_to_int(tok)
    pm = PREFIX_RE.match(stripped)
    if pm:
        return stripped, "prefix", int(pm.group(2))
    raise ValueError(f"not a folio: {raw!r}")


@dataclass(frozen=True)
class FolioInfo:
    folio: str | None          # printed folio, e.g. "iii", "42", "A-1", or None
    folio_style: str | None    # "roman" | "arabic" | "prefix" | None
    folio_tier: str | None     # "TIER_1" | "TIER_2" | "TIER_3" | "TIER_4" | None


@dataclass(frozen=True)
class ResolutionAudit:
    pdf_page: int
    tier1_label: str | None
    tier2_position: str | None        # "TL" | "TC" | "TR" | "BL" | "BC" | "BR"
    tier2_raw_text: str | None
    tier2_match: str | None
    tier3_inferred: str | None
    tier3_reason: str | None          # "blank" | "chapter_start" | "style_transition"
    tier4_anchor_page: int | None
    tier4_offset: int | None
    final_folio: str | None
    final_tier: str                   # "TIER_1" | "TIER_2" | "TIER_3" | "TIER_4" | "NONE"


# --- Tier 1: PDF /PageLabels expansion ---

def _expand_page_labels(doc: pymupdf.Document) -> dict[int, str]:
    """Apply /PageLabels rules to produce pdf_page -> label string.

    Per RESEARCH.md §B.1: rules are ordered by startpage; each extends until
    the next rule's startpage. We generate per-page labels deterministically.

    the reference corpus: returns {} (get_page_labels() is empty). For companion volumes
    with populated labels, this returns {0: "i", 1: "ii", ..., n: "M"} etc.
    """
    try:
        rules = doc.get_page_labels()
    except Exception:
        return {}
    if not rules:
        return {}
    # Sort by startpage ascending; each rule ends where the next begins.
    rules = sorted(rules, key=lambda r: int(r.get("startpage", 0)))
    labels: dict[int, str] = {}
    page_count = doc.page_count
    for i, rule in enumerate(rules):
        start = int(rule.get("startpage", 0))
        end = int(rules[i + 1]["startpage"]) if i + 1 < len(rules) else page_count
        prefix = rule.get("prefix", "") or ""
        style = rule.get("style", "") or ""
        first = int(rule.get("firstpagenum", 1))
        for offset, pdf_page in enumerate(range(start, end)):
            if not 0 <= pdf_page < page_count:
                continue
            num = first + offset
            body = _format_page_number(num, style)
            labels[pdf_page] = f"{prefix}{body}" if body else prefix
    return labels


def _format_page_number(n: int, style: str) -> str:
    if not style:
        return ""
    if style == "D":
        return str(n)
    if style == "r":
        return _int_to_roman(n).lower()
    if style == "R":
        return _int_to_roman(n)
    if style in ("a", "A"):
        # Alphabetic; wrap every 26: 1=a, 2=b, ..., 26=z, 27=aa, ...
        letters = []
        x = n
        while x > 0:
            x, rem = divmod(x - 1, 26)
            letters.append(chr(ord("a") + rem))
        body = "".join(reversed(letters))
        return body if style == "a" else body.upper()
    return str(n)


_INT_TO_ROMAN_TABLE = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]


def _int_to_roman(n: int) -> str:
    out = []
    for value, letter in _INT_TO_ROMAN_TABLE:
        while n >= value:
            out.append(letter)
            n -= value
    return "".join(out)


# --- Tier 2: Six-margin scan with verso/recto parity ---
#
# IMPLEMENTATION NOTE: We scan at span-level (not block-level) because the source book's
# running head packs the folio + chapter title into ONE block. The folio is its
# own span at the outer margin (x<=100 verso-left or x>=420 recto-right), with
# the running-head chapter title occupying the interior spans.
#
# Chapter letter-initial spans (e.g., "P" in "PLANNING") match the folio regex
# trivially — they're single characters. We reject single-letter candidates
# that are horizontally adjacent (<30pt gap) to another same-line span — those
# are letter-initials of a running-head phrase, not folios.


def _iter_spans(block: dict):
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            yield span


def _span_text(span: dict) -> str:
    return (span.get("text") or "")


def _span_position(span: dict, page_width: float) -> str:
    """Return "left" | "center" | "right" based on span x-center within page width."""
    x0, _, x1, _ = span["bbox"]
    cx = (x0 + x1) / 2.0
    if cx < page_width / 3.0:
        return "left"
    if cx > page_width * 2.0 / 3.0:
        return "right"
    return "center"


def _is_isolated_span(
    cand: dict, all_band_spans: list[dict], min_gap: float = 30.0
) -> bool:
    """Reject single-char candidates horizontally adjacent (<30pt gap) to another span.

    This filters out letter-initials like the 'P' in '§ 2 PLANNING BEFORE...'
    that happen to match the Roman regex (p is NOT in roman set but 'i','v','x',
    'l','c','d','m' all can appear as chapter-title initial caps: e.g.
    'C' in 'CROSS-EXAMINATION' matches `[ivxlcdm]+`).

    Multi-char candidates are always considered isolated.
    """
    cand_text = _span_text(cand).strip()
    if len(cand_text) >= 2:
        return True
    cand_bbox = cand["bbox"]
    for other in all_band_spans:
        if other is cand or other.get("bbox") == cand_bbox:
            continue
        other_bbox = other["bbox"]
        # Same baseline (y within 2pt)
        if abs(other_bbox[1] - cand_bbox[1]) >= 2.0:
            continue
        dx_right = other_bbox[0] - cand_bbox[2]  # other is to the right of cand
        dx_left = cand_bbox[0] - other_bbox[2]   # other is to the left of cand
        if (0 < dx_right < min_gap) or (0 < dx_left < min_gap):
            return False
    return True


def _pick_band_candidate(
    spans_in_band: list[dict], position: str, page_width: float
) -> tuple[str, str, int] | None:
    """Among spans in a band that match the folio regex at the given position,
    pick the most likely folio.

    Algorithm (ported from scripts/build_folio_fixture.py's _pick_folio):
      1. Filter spans by position (left/center/right third of the page width).
      2. Require span text to match the folio regex (Arabic or Roman).
      3. Apply isolation filter: single-char spans adjacent to another same-line
         span are chapter-title initials, not folios.
      4. Prefer the candidate at the outermost edge of its position band
         (x<=100 for left, x>=420 for right).
      5. Among equivalent candidates, longer text beats shorter (multi-char
         romans / arabics beat single-digit ties).

    Returns (folio_str, folio_style, int_value) or None.
    """
    # Filter: position and regex
    candidates: list[dict] = []
    for s in spans_in_band:
        text = _span_text(s).strip()
        if not text:
            continue
        if _span_position(s, page_width) != position:
            continue
        try:
            parse_folio(text)
        except ValueError:
            continue
        candidates.append(s)
    if not candidates:
        return None

    # Isolation filter (drops chapter-title letter initials).
    isolated = [c for c in candidates if _is_isolated_span(c, spans_in_band)]
    if not isolated:
        return None

    # Edge preference: pick the span closest to the outer margin.
    if position == "left":
        isolated.sort(key=lambda c: (
            -len(_span_text(c).strip()),  # longer text wins
            c["bbox"][0],                  # smaller x wins (closer to left edge)
        ))
    elif position == "right":
        isolated.sort(key=lambda c: (
            -len(_span_text(c).strip()),  # longer text wins
            -c["bbox"][2],                 # larger x1 wins (closer to right edge)
        ))
    else:  # center
        isolated.sort(key=lambda c: (
            -len(_span_text(c).strip()),
            abs((c["bbox"][0] + c["bbox"][2]) / 2.0 - page_width / 2.0),
        ))

    chosen = isolated[0]
    chosen_text = _span_text(chosen).strip()
    folio_str, folio_style, intval = parse_folio(chosen_text)
    return folio_str, folio_style, intval


def tier2_scan(
    extractions: Sequence[PageExtraction],
    y_bands: YBands,
) -> dict[int, tuple[str, str, str, str]]:
    """Return {pdf_page: (folio, folio_style, position, raw_text)}.

    Cross-page consistency filter is applied: only candidates that form a
    strictly-monotonically-increasing sequence across same-parity pages
    (verso/recto) within the same position+style survive. This filters out
    false positives like chapter numbers or section numbers (which do not
    monotonically increase every 2 pages at a fixed margin position).

    Positions: TL/TC/TR in the top band (y_top < top_cutoff),
               BL/BC/BR in the bottom band (y_top >= bot_cutoff).

    Verso = even pdf_page; recto = odd. the source book's empirical layout is
    TL-verso (roman/arabic) and TR-recto (roman/arabic) per RESEARCH §B.2.
    """
    # Step 1: gather raw per-position candidates per page.
    # per_page[pdf_page][position_code] = (folio_str, folio_style, int_value, raw_text)
    per_page: dict[int, dict[str, tuple[str, str, int, str]]] = {}
    for ex in extractions:
        spans_top: list[dict] = []
        spans_bot: list[dict] = []
        # Bottom-band fallback: if y_bands reports no bottom cluster
        # (bot_cutoff >= page_height, which is the source book's reality — no
        # running foot on body pages), use a fixed 90% threshold so chapter-
        # start pages (which DO print folios in the running foot at y~671)
        # are still caught. This preserves the six-margin contract.
        bot_threshold = y_bands.bot_cutoff
        if bot_threshold >= ex.height:
            bot_threshold = ex.height * 0.9
        for b in ex.dict_output.get("blocks", []):
            if b.get("type") != 0:
                continue
            for s in _iter_spans(b):
                y_top = float(s["bbox"][1])
                if y_top < y_bands.top_cutoff:
                    spans_top.append(s)
                elif y_top >= bot_threshold:
                    spans_bot.append(s)
        per_page[ex.pdf_page] = {}
        for position_code, band_spans in (
            ("TL", spans_top), ("TC", spans_top), ("TR", spans_top),
            ("BL", spans_bot), ("BC", spans_bot), ("BR", spans_bot),
        ):
            pos_word = {"L": "left", "C": "center", "R": "right"}[position_code[1]]
            got = _pick_band_candidate(band_spans, pos_word, ex.width)
            if got is not None:
                folio_str, folio_style, intval = got
                per_page[ex.pdf_page][position_code] = (
                    folio_str, folio_style, intval, folio_str,
                )

    # Step 2: cross-page consistency filter.
    # Split each position by parity (verso=even, recto=odd) and by style.
    # Keep sequences that are strictly monotonically increasing in int value.
    # Note: allow sequences of length 1 if the position has no conflicting
    # evidence from the opposite parity (handles short PDFs).
    resolved: dict[int, tuple[str, str, str, str]] = {}
    hits_by_pos_parity_style: dict[tuple[str, str, str], list[tuple[int, str, str, int, str]]] = defaultdict(list)
    for pdf_page, by_pos in per_page.items():
        for position_code, (folio_str, folio_style, intval, raw) in by_pos.items():
            parity = "verso" if pdf_page % 2 == 0 else "recto"
            hits_by_pos_parity_style[(position_code, parity, folio_style)].append(
                (pdf_page, folio_str, folio_style, intval, raw)
            )

    for (position_code, _parity, _style), subset in hits_by_pos_parity_style.items():
        subset.sort(key=lambda t: t[0])
        if not subset:
            continue
        # Single-entry subsets: keep them (the reference corpus has no adversarial noise
        # at span-level now that we've filtered letter-initials).
        if len(subset) == 1:
            p, f, s, _v, raw = subset[0]
            resolved.setdefault(p, (f, s, position_code, raw))
            continue
        # Require strictly monotonic int values across the parity subset.
        is_mono = all(
            subset[i + 1][3] > subset[i][3] for i in range(len(subset) - 1)
        )
        if not is_mono:
            continue
        for p, f, s, _v, raw in subset:
            resolved.setdefault(p, (f, s, position_code, raw))
    return resolved


# --- Blank-page detection (used by Tier-3 to leave blanks as null) ---

def _detect_blank_pages(extractions: Sequence[PageExtraction]) -> set[int]:
    """Return pdf_pages with no visible text content.

    A "blank" page is one where every text span has only whitespace (or no
    text spans at all). This signals that no folio is printed on the page
    AND that interpolation across the page is unsafe — the book counts the
    blank in its folio numbering but does not print a folio, so the resolver
    should return null rather than inferring a phantom folio via Tier-3.
    """
    blanks: set[int] = set()
    for ex in extractions:
        has_content = False
        for b in ex.dict_output.get("blocks", []):
            if b.get("type") != 0:
                continue
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    if (span.get("text") or "").strip():
                        has_content = True
                        break
                if has_content:
                    break
            if has_content:
                break
        if not has_content:
            blanks.add(ex.pdf_page)
    return blanks


# --- Monotonicity check (D-05, FOL-05) ---

def _folio_int(folio: str | None, style: str | None) -> int | None:
    if folio is None or style is None:
        return None
    try:
        _, _, v = parse_folio(folio)
    except ValueError:
        return None
    return v


def _int_to_folio(n: int, style: str) -> str:
    if style == "arabic":
        return str(n)
    if style == "roman":
        return _int_to_roman(n).lower()
    raise ValueError(f"cannot emit style={style!r} from contiguity inference")


def check_monotonicity(resolved: dict[int, "FolioInfo"]) -> None:
    """Raise FolioMonotonicityError on regression within a style section.

    Null-folio pages (blank / unnumbered) are skipped.
    Implausible gaps (>2 PDF pages with no folio between two folios that would
    skip >2 folios of value) raise FolioContiguityError.

    D-05 / FOL-05: within each folio-style section, folios must increase
    monotonically with PDF page ordinal. The error message names BOTH
    offending pdf_pages + their folios (architectural lock #4: folio + pdf_page
    for forensic clarity).
    """
    by_style: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for pdf_page, info in sorted(resolved.items()):
        if info.folio is None or info.folio_style is None:
            continue
        val = _folio_int(info.folio, info.folio_style)
        if val is None:
            continue
        by_style[info.folio_style].append((pdf_page, val))

    for style, pairs in by_style.items():
        for i in range(len(pairs) - 1):
            (p0, f0), (p1, f1) = pairs[i], pairs[i + 1]
            if f1 <= f0:
                raise FolioMonotonicityError(
                    f"Folio regression in style={style!r}: "
                    f"pdf_page {p0} has folio {f0} but pdf_page {p1} has folio {f1}"
                )
            # Allow up to (p1-p0) + 2 extra folios of gap (for transition pages).
            if f1 - f0 > (p1 - p0) + 2:
                raise FolioContiguityError(
                    f"Implausible folio gap in style={style!r}: "
                    f"pdf_page {p0} -> folio {f0}, pdf_page {p1} -> folio {f1} "
                    f"(gap of {f1 - f0} folios over {p1 - p0} PDF pages)"
                )


# --- Public resolver ---

class CascadeFolioResolver:
    """4-tier folio cascade: PageLabels -> margin scan -> contiguity -> anchor offset.

    The resolver eagerly resolves every pdf_page on construction, running the
    monotonicity assertion at the end. Callers get `resolve(pdf_page)`,
    `resolve_all()`, and `audit_rows()`.
    """

    def __init__(
        self,
        doc: pymupdf.Document,
        extractions: Sequence[PageExtraction],
        y_bands: YBands,
    ) -> None:
        self._doc = doc
        self._extractions = list(extractions)
        self._page_count = doc.page_count
        self._y_bands = y_bands
        self._tier1: dict[int, str] = _expand_page_labels(doc)
        self._tier2: dict[int, tuple[str, str, str, str]] = tier2_scan(extractions, y_bands)
        self._blank_pages: set[int] = _detect_blank_pages(extractions)
        self._resolved: dict[int, FolioInfo] = {}
        self._audit: list[ResolutionAudit] = []
        self._compute_all()

    def _compute_all(self) -> None:
        """Run Tier-1 -> Tier-2 -> Tier-3 -> Tier-4; build audit; assert monotonicity."""
        tier_used: dict[int, str] = {}
        folios: dict[int, tuple[str | None, str | None]] = {
            p: (None, None) for p in range(self._page_count)
        }

        # Tier 1: /PageLabels rules (dead branch on the reference corpus; alive for companions).
        for p, label in self._tier1.items():
            if not label:
                continue
            try:
                f, s, _ = parse_folio(label)
                folios[p] = (f, s)
                tier_used[p] = "TIER_1"
            except ValueError:
                pass

        # Tier 2: margin-scan hits (only fills pages Tier-1 missed).
        for p, (f, s, _pos, _raw) in self._tier2.items():
            if p not in tier_used:
                folios[p] = (f, s)
                tier_used[p] = "TIER_2"

        # Tier 3: contiguity inference between adjacent confident anchors.
        tier3_reasons: dict[int, str] = {}
        tier3_inferred: dict[int, str] = {}
        known = sorted(
            (p, folios[p][0], folios[p][1]) for p in folios
            if folios[p][0] is not None and folios[p][1] is not None
        )
        for i in range(len(known) - 1):
            pa, fa, sa = known[i]
            pb, fb, sb = known[i + 1]
            gap_pages = pb - pa
            if gap_pages <= 1:
                continue  # no interior pages to fill
            if sa != sb:
                # Style transition (e.g., Roman -> Arabic): leave interior null.
                for q in range(pa + 1, pb):
                    tier3_reasons[q] = "style_transition"
                continue
            a_int = _folio_int(fa, sa)
            b_int = _folio_int(fb, sb)
            if a_int is None or b_int is None:
                continue
            expected_gap = b_int - a_int
            if expected_gap == gap_pages:
                # Fully contiguous: fill every interior page with +1 chain,
                # EXCEPT physically blank pages (the book counts the blank in
                # its folio sequence but doesn't print the folio — fixture
                # tracks printed folios only, so we leave blanks as null).
                for offset, q in enumerate(range(pa + 1, pb), start=1):
                    if q in tier_used:
                        continue
                    if q in self._blank_pages:
                        tier3_reasons[q] = "blank_page"
                        continue
                    filled = _int_to_folio(a_int + offset, sa)
                    folios[q] = (filled, sa)
                    tier_used[q] = "TIER_3"
                    tier3_inferred[q] = filled
                    tier3_reasons[q] = "contiguous_fill"
            elif expected_gap == gap_pages - 1:
                # One interior page is blank / chapter-start suppressed.
                # If we can identify the blank, skip that specific page and
                # chain the remaining pages normally. Otherwise default to
                # skipping pa+1 (chapter-start convention).
                blank_in_gap = [
                    q for q in range(pa + 1, pb) if q in self._blank_pages
                ]
                if len(blank_in_gap) == 1:
                    skip_page = blank_in_gap[0]
                    for offset, q in enumerate(range(pa + 1, pb), start=1):
                        if q in tier_used:
                            continue
                        if q == skip_page:
                            tier3_reasons[q] = "blank_page"
                            continue
                        # Rank-within-gap is the offset minus any blanks skipped so far.
                        rank = sum(
                            1 for qq in range(pa + 1, q + 1)
                            if qq not in self._blank_pages
                        )
                        filled = _int_to_folio(a_int + rank, sa)
                        folios[q] = (filled, sa)
                        tier_used[q] = "TIER_3"
                        tier3_inferred[q] = filled
                        tier3_reasons[q] = "chapter_start_or_blank"
                else:
                    for offset, q in enumerate(range(pa + 1, pb), start=1):
                        if q in tier_used:
                            continue
                        if offset == 1 and gap_pages == 2:
                            tier3_reasons[q] = "chapter_start_or_blank"
                            continue
                        filled = _int_to_folio(a_int + offset - 1, sa)
                        folios[q] = (filled, sa)
                        tier_used[q] = "TIER_3"
                        tier3_inferred[q] = filled
                        tier3_reasons[q] = "chapter_start_or_blank"
            else:
                # Larger skip (implausible); leave interior null and flag.
                for q in range(pa + 1, pb):
                    tier3_reasons[q] = "implausible_gap"

        # Tier 4: offset from nearest confident anchor (last resort).
        tier4_info: dict[int, tuple[int, int]] = {}
        anchors = sorted(p for p, t in tier_used.items() if t in ("TIER_1", "TIER_2"))
        for pdf_page in range(self._page_count):
            if pdf_page in tier_used:
                continue
            if pdf_page in self._blank_pages:
                tier3_reasons.setdefault(pdf_page, "blank_page")
                continue  # blanks stay null
            if tier3_reasons.get(pdf_page) in (
                "style_transition", "chapter_start_or_blank", "implausible_gap",
                "blank_page",
            ):
                continue  # intentionally unfilled
            if not anchors:
                continue
            # Nearest anchor by PDF-page distance; ties -> smaller pdf_page.
            anchor = min(anchors, key=lambda a: (abs(a - pdf_page), a))
            fa, sa = folios[anchor]
            if fa is None or sa is None:
                continue
            a_int = _folio_int(fa, sa)
            if a_int is None:
                continue
            offset = pdf_page - anchor
            target_val = a_int + offset
            # Guard: refuse to extrapolate to non-positive folio values.
            # This prevents Tier-4 from producing bogus folios (e.g., "ii" on
            # pg 3 when the real layout has pg 3 as unnumbered copyright).
            # Also guard against crossing into the previous-style region when
            # extrapolating backwards past the style-transition boundary.
            if target_val <= 0:
                continue
            # Don't extrapolate backwards across a style boundary: if the
            # anchor is beyond the current page AND there is a smaller-pdf_page
            # anchor of a different style, leave this page null.
            if offset < 0:
                smaller_anchors = [a for a in anchors if a < pdf_page]
                if smaller_anchors:
                    prev_anchor = max(smaller_anchors)
                    _, prev_style = folios[prev_anchor]
                    if prev_style != sa:
                        continue
                else:
                    # No anchor before this page; backwards extrapolation is
                    # guessing into unknown territory — refuse.
                    continue
            try:
                filled = _int_to_folio(target_val, sa)
            except ValueError:
                continue
            folios[pdf_page] = (filled, sa)
            tier_used[pdf_page] = "TIER_4"
            tier4_info[pdf_page] = (anchor, offset)

        # Build FolioInfo + audit rows.
        self._resolved = {}
        self._audit = []
        for pdf_page in range(self._page_count):
            f, s = folios[pdf_page]
            tier = tier_used.get(pdf_page)
            self._resolved[pdf_page] = FolioInfo(
                folio=f, folio_style=s, folio_tier=tier,
            )
            tier2_data = self._tier2.get(pdf_page)
            self._audit.append(ResolutionAudit(
                pdf_page=pdf_page,
                tier1_label=self._tier1.get(pdf_page),
                tier2_position=(tier2_data[2] if tier2_data else None),
                tier2_raw_text=(tier2_data[3] if tier2_data else None),
                tier2_match=(tier2_data[0] if tier2_data else None),
                tier3_inferred=tier3_inferred.get(pdf_page),
                tier3_reason=tier3_reasons.get(pdf_page),
                tier4_anchor_page=(tier4_info[pdf_page][0] if pdf_page in tier4_info else None),
                tier4_offset=(tier4_info[pdf_page][1] if pdf_page in tier4_info else None),
                final_folio=f,
                final_tier=tier or "NONE",
            ))

        # D-05 / FOL-05: monotonicity assertion (build-time gate).
        check_monotonicity(self._resolved)

    def resolve(self, pdf_page: int) -> FolioInfo:
        if not 0 <= pdf_page < self._page_count:
            raise IndexError(
                f"pdf_page {pdf_page} out of range [0, {self._page_count})"
            )
        return self._resolved[pdf_page]

    def resolve_all(self) -> dict[int, FolioInfo]:
        return dict(self._resolved)

    def audit_rows(self) -> list[ResolutionAudit]:
        return list(self._audit)

    def write_audit_jsonl(self, path) -> None:
        """Write per-page tier/decision/winner record to JSONL.

        Deterministic: pages sorted by pdf_page, keys sorted within each row.
        Used by plan 05 for the coverage report and forensic replay.
        """
        import json
        from dataclasses import asdict
        from pathlib import Path

        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for row in self._audit:
                fh.write(json.dumps(asdict(row), sort_keys=True))
                fh.write("\n")
