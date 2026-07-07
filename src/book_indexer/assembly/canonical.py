"""D-01 strict-tiebreaker canonical-form chooser.

Pure function over a ``BucketCandidate``. Returns the chosen canonical
surface form. Tiebreaker order (RESEARCH §"specifics" verbatim, D-01):

  1. Longest spelled-out form (acronyms last; an acronym is selected
     ONLY when no spelled-out form exists).
  2. Lowest ``section_ref`` at first appearance (per ``surface_provenance``).
  3. Lowest ``pdf_page`` within section.
  4. Lowest ``token_index`` within page.
  5. Alphabetical fallthrough on surface (final determinism guarantee per
     Pitfall §P-1; ensures unit tests with empty ``surface_provenance`` are
     still deterministic).

"Valid spelled-out" means: ``lemmatize(form.lower().strip())`` produces
the same lemma_key as the bucket's lemma_key. Hyphenation and
capitalization variants all count as valid (the lemma collapses them).

Stop-words filter (D-01): leading articles ("the", "a", "an") are
stripped before length comparison. Non-leading articles and other
function words ("of", "in", "for") are NOT stripped — they're meaningful
in legal phrases like "rule of evidence", "burden of proof".

requirements_addressed: ASM-01.
"""
from __future__ import annotations

from .dedup import (
    BucketCandidate,
    SurfaceProvenance,
    lemma_bucket_key,
)

_ARTICLES = ("the", "a", "an")


def strip_leading_article(s: str) -> str:
    """D-01 stop-words filter: strip ONE leading article if present.

    Multi-word phrases only: "the hearsay rule" → "hearsay rule". A
    single-word "a" or "the" returns unchanged (defensive — would otherwise
    return empty string and corrupt length comparisons). Case-insensitive
    on the head; preserves the rest of the string verbatim (no lowercasing
    of the tail — display fidelity matters for canonical output).
    """
    parts = s.strip().split(maxsplit=1)
    if len(parts) < 2:
        return s
    head, tail = parts[0].lower(), parts[1]
    if head in _ARTICLES:
        return tail
    return s


def is_valid_spelled_out(surface: str, lemma_key: str, nlp) -> bool:
    """A surface is a "valid spelled-out form" iff its ``lemma_bucket_key``
    equals the bucket's ``lemma_key``.

    Used to ensure D-01 step 1 only considers forms within the bucket;
    defensive — ``dedup.build_buckets`` already filters this, but
    ``canonical.py`` is a pure function called from ``tree.py`` and may
    receive unfiltered surfaces in tests.
    """
    return lemma_bucket_key(surface, nlp) == lemma_key


# Sort sentinel: any character that sorts AFTER all printable ASCII so
# unknown-provenance surfaces sort LAST in tiebreaker tuples (determinism
# preserved via the alphabetical-surface 4th element).
_UNKNOWN_PROVENANCE_SECTION = "￿"


def _provenance_key(
    surface: str,
    provenance: dict[str, SurfaceProvenance],
) -> tuple[str, int, int, str]:
    """Build a sortable tuple for tiebreakers 2-4.

    Surface used as 4th element so equal-provenance surfaces sort
    alphabetically (final determinism guarantee per Pitfall §P-1).
    Unknown-provenance surfaces sort AFTER known via the sentinel
    ``_UNKNOWN_PROVENANCE_SECTION`` and ``10**9`` page/token sentinels.
    """
    prov = provenance.get(surface)
    if prov is None:
        return (_UNKNOWN_PROVENANCE_SECTION, 10**9, 10**9, surface)
    return (prov.section_ref, prov.pdf_page, prov.token_index, surface)


def _is_acronym_surface(s: str) -> bool:
    """Heuristic: all-uppercase letters, length 2-6, no spaces, no leading
    article. Used to partition surfaces into spelled-out vs acronym for
    D-01 step 1.
    """
    stripped = strip_leading_article(s).strip()
    return bool(
        stripped
        and " " not in stripped
        and 2 <= len(stripped) <= 6
        and stripped.isalpha()
        and stripped.upper() == stripped
    )


def elect_canonical(bucket: BucketCandidate, nlp=None) -> str:
    """Apply D-01 tiebreakers; return the chosen canonical surface.

    Args:
        bucket: ``BucketCandidate`` from ``dedup.build_buckets``. Must have
            ``surfaces`` non-empty.
        nlp: optional spaCy Language. Required only if the caller wants
            strict ``is_valid_spelled_out`` enforcement (defensive). When
            ``None``, every surface is treated as candidate-canonical
            (faster path; ``build_buckets`` already validated bucket
            membership via ``lemma_bucket_key``).

    Returns:
        The chosen canonical surface (preserving its original casing and
        hyphenation verbatim — this is the display string).

    Raises:
        ValueError: if ``bucket.surfaces`` is empty.
    """
    if not bucket.surfaces:
        raise ValueError(f"bucket {bucket.lemma_key!r} has no surfaces")

    # Step 1: partition spelled-out vs acronym surfaces.
    spelled_out: list[str] = []
    acronyms: list[str] = []
    for s in bucket.surfaces:
        (acronyms if _is_acronym_surface(s) else spelled_out).append(s)

    # D-01 step 1: spelled-out wins when present, acronym only as fallback.
    candidates = spelled_out if spelled_out else acronyms
    if not candidates:
        # Shouldn't happen given input had >= 1 surface, but defensive:
        candidates = list(bucket.surfaces)

    # Optional strict enforcement: if nlp provided, narrow to surfaces whose
    # lemma_key actually equals the bucket's lemma_key.
    if nlp is not None and spelled_out:
        valid = [s for s in candidates if is_valid_spelled_out(s, bucket.lemma_key, nlp)]
        if valid:
            candidates = valid

    def length_key(s: str) -> int:
        return len(strip_leading_article(s).strip())

    max_len = max(length_key(s) for s in candidates)
    finalists = [s for s in candidates if length_key(s) == max_len]

    if len(finalists) == 1:
        return finalists[0]

    # Steps 2-4 (with alphabetical 5th-element fallthrough).
    finalists.sort(key=lambda s: _provenance_key(s, bucket.surface_provenance))
    return finalists[0]
