"""eyecite-driven case extraction (FullCaseCitation).

Pure function over text. Does NOT call ``verify()``, does NOT write to the
evidence ledger, does NOT construct Pydantic IR. Plan 03's verifier_bridge
+ __main__ wire the output of ``scan_cases()`` into the full pipeline.

Pitfall P-1 (RESEARCH §H-14): eyecite emits ~264 ``UnknownCitation``
records on the reference corpus, all bare ``§`` glyphs from internal section
refs (e.g., ``§3.07.3.``). Filter them aggressively.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from eyecite import get_citations
from eyecite.models import FullCaseCitation, UnknownCitation

# P-1 filter: drop UnknownCitation matched_text matching these shapes.
#  - bare ``§`` or ``§§`` (with optional whitespace)
#  - internal section ref ``§N`` / ``§N.NN`` / ``§N.NN.N`` (optional trailing dot)
_UNKNOWN_NOISE_PATTERN = re.compile(
    r"^§+\s*$|^§\d+(\.\d{2}(\.\d+)?)?\.?$"
)
_UNKNOWN_NOISE_LEN_MAX = 8


@dataclass(frozen=True)
class RawCaseHit:
    """A single raw case citation found in source text.

    Pure data — no Pydantic. Plan 03 wraps this into a ``CaseEntry`` after
    ``verify()`` succeeds (no auto-verification here).
    """

    display_name: str
    canonical_citation: str
    reporter: str
    court: str | None
    year: int | None
    surface_form: str
    pdf_page: int
    char_offset: int


def _safe_year(raw: object) -> int | None:
    """Coerce eyecite ``metadata.year`` (str | None) to a sane int.

    Returns None if unparseable, missing, or outside [1700, 2100]
    (sanity guard against extracted noise).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s.isdigit() and 1700 <= int(s) <= 2100:
        return int(s)
    return None


def _is_unknown_noise(c: UnknownCitation) -> bool:
    """Return True if this UnknownCitation is one of the 264 known false
    positives (bare § glyphs / internal section refs).
    """
    text = c.matched_text() or ""
    if len(text) > _UNKNOWN_NOISE_LEN_MAX:
        return False
    return bool(_UNKNOWN_NOISE_PATTERN.match(text.strip()))


def scan_cases(text: str, *, pdf_page: int) -> list[RawCaseHit]:
    """Scan ``text`` for case citations via eyecite.

    Returns hits sorted by ``char_offset`` ascending. UnknownCitation
    noise (Pitfall P-1) is dropped silently; legitimate UnknownCitations
    (rare on this corpus) are also dropped — they cannot be verified
    without further human curation, so the cases extractor only emits
    confirmed FullCaseCitations.
    """
    if not text:
        return []

    cs = get_citations(text)
    hits: list[RawCaseHit] = []

    for c in cs:
        if isinstance(c, UnknownCitation):
            # Defense-in-depth: log-or-drop. We drop silently because the
            # entire 264-cite UnknownCitation surface on the reference corpus is
            # known-noise. Plan 04 (ship-blockers) re-checks unverified
            # extractions explicitly.
            if _is_unknown_noise(c):
                continue
            continue
        if not isinstance(c, FullCaseCitation):
            continue

        md = c.metadata
        plaintiff = (md.plaintiff or "").strip() or "Unknown Plaintiff"
        defendant = (md.defendant or "").strip() or "Unknown Defendant"
        display_name = f"{plaintiff} v. {defendant}"
        span = c.span()
        hits.append(
            RawCaseHit(
                display_name=display_name,
                canonical_citation=c.corrected_citation(),
                reporter=c.corrected_reporter() or "",
                court=md.court or None,
                year=_safe_year(md.year),
                surface_form=c.matched_text() or display_name,
                pdf_page=pdf_page,
                char_offset=span[0] if span else 0,
            )
        )

    hits.sort(key=lambda h: h.char_offset)
    return hits
