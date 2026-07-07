"""B-01 prose-form expansion + B-02 whitespace collapse.

Stateless helpers consumed by ``dedup.py`` at intake. Per CONTEXT.md D-06,
Phase 4 absorbs B-01 (prose-form rule normalization) and B-02 (statute
newline collapse) without modifying Phase 3b's ``tables/*.json`` artifacts.

Per RESEARCH §B-01 the prose-form recovery rate is 4/30 (13%) — not
"mostly recover" as CONTEXT.md predicted. The primary VALUE of B-01 is
NORMALIZATION at the dedup stage: a NER candidate "Federal Rule of
Evidence 706" maps to the lemma bucket ``fre 706``, joining the
abbreviated "FRE 706" extractions. See RESEARCH §B-01 Recommendation
block for the full rationale.

Public API:
    prose_to_canonical(surface) -> str | None
    collapse_whitespace(citation) -> str
    PROSE_RULE_PATTERNS  # list[(re.Pattern, str)] — exposed for reuse

requirements_addressed: ASM-01 (canonical-form selection), implicit D-06.
"""
from __future__ import annotations

import re

# Patterns derived from RESEARCH §"specifics" B-01 block. Mirrors the
# structure of Phase 3b's ``regex_fallback`` patterns. Order matters:
# more-specific patterns first; MRPC last so "Model Rule" doesn't shadow
# "Federal Rule" (different prefixes anyway, but explicit ordering is
# defense-in-depth).
PROSE_RULE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\bFederal\s+Rule(?:s)?\s+of\s+Evidence\s+"
            r"(\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)",
            re.IGNORECASE,
        ),
        "FRE",
    ),
    (
        re.compile(
            r"\bFederal\s+Rule(?:s)?\s+of\s+Civil\s+Procedure\s+"
            r"(\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)",
            re.IGNORECASE,
        ),
        "FRCP",
    ),
    (
        re.compile(
            r"\bFederal\s+Rule(?:s)?\s+of\s+Appellate\s+Procedure\s+"
            r"(\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)",
            re.IGNORECASE,
        ),
        "FRAP",
    ),
    (
        re.compile(
            r"\bModel\s+Rule(?:s)?(?:\s+of\s+Professional\s+Conduct)?\s+"
            r"(\d+(?:\.\d+)*(?:\([a-z0-9]+\))*)",
            re.IGNORECASE,
        ),
        "MRPC",
    ),
]


def prose_to_canonical(surface: str) -> str | None:
    """Map a prose-form rule reference to its canonical citation form.

    Returns the canonical form (e.g. ``"FRE 706"``) on a match, else
    ``None``. Tolerates leading/trailing whitespace; tolerates internal
    multi-whitespace (the pattern uses ``\\s+`` between tokens, so
    line-broken manuscript prose still matches).
    """
    if not surface or not surface.strip():
        return None
    for pat, sys_code in PROSE_RULE_PATTERNS:
        m = pat.search(surface)
        if m:
            return f"{sys_code} {m.group(1)}"
    return None


_WHITESPACE_RE = re.compile(r"\s+")


def collapse_whitespace(citation: str) -> str:
    """B-02: collapse internal whitespace (incl. ``\\n``, ``\\t``) to a single
    space; strip leading/trailing whitespace.

    Used at intake to dedupe ``28 U.S.C. Sec. \\n1407`` against
    ``28 U.S.C. Sec. 1407`` so they map to the same canonical bucket.
    Empty input returns empty.
    """
    if not citation:
        return citation
    return _WHITESPACE_RE.sub(" ", citation).strip()
