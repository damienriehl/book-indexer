"""eyecite-driven statute extraction (FullLawCitation) + Constitution regex.

Pure function over text. The constitutional regex output (from
``regex_fallback.scan_constitution``) is wrapped into ``RawStatuteHit``
records so Plan 03's ``__main__`` has a single statute pipeline to feed
``verifier_bridge.verify_statute()``.

Jurisdiction gating (D-07): only reporters listed in
``fixtures/citation_jurisdictions.yaml`` are accepted. the reference corpus has
``jurisdictions=['us']`` which enables ``U.S.C.`` / ``U.S.C.A.`` /
``U.S.C.S.``.
"""
from __future__ import annotations

from dataclasses import dataclass

from eyecite import get_citations
from eyecite.models import FullLawCitation

from .regex_fallback import scan_constitution

# Jurisdiction → enabled statute reporters. Mirrors the ``us`` row in
# fixtures/citation_jurisdictions.yaml. Future state-code expansion plugs
# in here, gated by the YAML — the gating logic is identical.
_US_REPORTERS = {"U.S.C.", "U.S.C.A.", "U.S.C.S."}


@dataclass(frozen=True)
class RawStatuteHit:
    """A single raw statute citation found in source text.

    Pure data — no Pydantic. Plan 03 wraps this into a ``StatuteEntry``
    after ``verify()`` succeeds.

    Constitution hits (Amendment / article) use ``title="Const."`` as a
    sentinel so consumers can route them differently if needed.
    """

    display_name: str
    canonical_citation: str
    title: str
    section: str
    publisher: str | None
    surface_form: str
    pdf_page: int
    char_offset: int


def _enabled_reporters(jurisdictions: list[str]) -> set[str]:
    """Compute the set of statute reporters allowed for these jurisdictions.

    the reference corpus: only ``us`` enables anything. Future state codes
    (e.g., ``nj`` → ``N.J. Rev. Stat.``) plug in here.
    """
    enabled: set[str] = set()
    if "us" in jurisdictions:
        enabled |= _US_REPORTERS
    return enabled


def scan_statutes(
    text: str,
    *,
    pdf_page: int,
    jurisdictions: list[str],
) -> list[RawStatuteHit]:
    """Scan ``text`` for statute citations.

    Two sources, combined:
    1. eyecite ``FullLawCitation`` (filtered to enabled reporters).
    2. ``regex_fallback.scan_constitution`` (Amendment + article).

    Returns hits sorted by ``char_offset`` ascending.
    """
    if not text:
        return []

    enabled = _enabled_reporters(jurisdictions)
    hits: list[RawStatuteHit] = []

    # Source 1: eyecite FullLawCitations, gated by reporter ∈ enabled.
    cs = get_citations(text)
    for c in cs:
        if not isinstance(c, FullLawCitation):
            continue
        reporter = (c.groups.get("reporter") or "").strip()
        if reporter not in enabled:
            continue
        span = c.span()
        hits.append(
            RawStatuteHit(
                display_name=c.corrected_citation(),
                canonical_citation=c.corrected_citation(),
                title=str(c.groups.get("title", "")),
                section=str(c.groups.get("section", "")),
                publisher=c.groups.get("publisher"),
                surface_form=c.matched_text() or "",
                pdf_page=pdf_page,
                char_offset=span[0] if span else 0,
            )
        )

    # Source 2: Constitution via regex (always-on for jurisdictions
    # containing 'us'; the U.S. Constitution is part of the federal
    # surface).
    if "us" in jurisdictions:
        for h in scan_constitution(text, pdf_page=pdf_page):
            display = h["display_name"]
            section = ""
            if h["kind"] == "article" and "§" in display:
                section = display.rsplit("§", 1)[-1].strip()
            hits.append(
                RawStatuteHit(
                    display_name=display,
                    canonical_citation=display,
                    title="Const.",
                    section=section,
                    publisher=None,
                    surface_form=h["surface_form"],
                    pdf_page=pdf_page,
                    char_offset=h["char_offset"],
                )
            )

    hits.sort(key=lambda h: h.char_offset)
    return hits
