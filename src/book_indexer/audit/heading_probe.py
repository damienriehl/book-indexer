"""COV-01 / D-08 heading-cased title-form probe.

For every ``sections.title`` row, extract candidate noun phrases by stripping
leading imperative verbs and trailing prepositional / common-stop tokens. The
trailing 2-3 content words become probe candidates.

Source: 08-RESEARCH.md §Pattern 2 (verbatim ``LEAD_VERBS`` / ``COMMON_STOPS``).

Sensitivity filtering (RESEARCH §Pitfall 4) — additional gates applied at the
caller level (``scripts/audit_zero_evidence_drops.py::_heading_probe_pass``):
    1. ``ProbeCandidate.n_tokens >= 2`` (this module enforces).
    2. Phrase contains >= 1 non-COMMON_STOP word (caller re-checks).
    3. Phrase verifies against >= 2 distinct sections (caller calls ``verify``).
    4. Phrase's ``canonical_form_key`` is not already an entry in
       ``index_tree.json::entries`` (caller re-checks).

Architecture Lock #1: this module never constructs ``Evidence`` — it only
emits ``ProbeCandidate`` rows for downstream verification.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

# Imperative verbs commonly leading textbook section titles. Stripping them
# leaves the noun-phrase tail that's the actual concept.
LEAD_VERBS = frozenset({
    "define", "describe", "discuss", "explain", "consider", "use",
    "plan", "prepare", "present", "employ", "select", "address", "avoid",
    "analyze", "review", "apply", "conduct", "establish", "identify",
    "choose", "find", "understand", "evaluate", "create", "develop",
    "build", "introduce", "propose", "how", "what", "who", "why", "when",
    "be", "do", "maintain", "enjoy", "characterize",
})

# High-frequency English stop tokens. Trailing instances are stripped; trailing
# stops without content collapse the phrase below the n_tokens=2 floor.
COMMON_STOPS = frozenset({
    "a", "an", "the", "of", "to", "and", "or", "in", "for", "with",
    "on", "as", "by", "at", "is", "are",
})

# Word grammar tolerant of hyphens and apostrophes (cross-examination, attorney's).
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")


class ProbeCandidate(BaseModel):
    """One heading-derived probe candidate. Frozen + locator-free."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)

    section_ref: str = Field(min_length=1)
    title: str = Field(min_length=1)
    phrase: str = Field(min_length=1)
    n_tokens: int = Field(ge=2)


def heading_probe_candidates(
    sections_rows: Iterable[tuple[str, str]],
) -> list[ProbeCandidate]:
    """Yield candidate noun phrases from each ``(section_ref, title)`` row.

    For each row, strip leading LEAD_VERBS and trailing COMMON_STOPS, then take
    the trailing 2 and 3 content words (longer phrases are too noisy per
    RESEARCH §Pattern 2). Returns a deterministic list ordered by input order
    then by ``n_tokens`` (3 before 2 — caller may re-sort downstream).
    """
    out: list[ProbeCandidate] = []
    for section_ref, title in sections_rows:
        if not title:
            continue
        words = _WORD_RE.findall(title.strip())
        if len(words) < 2:
            continue
        # Strip leading imperative verbs ("Define X", "Describe Y") AND
        # leading stops ("a", "an", "the", "of") — "Why Be an Advocate" →
        # strip "why" + "be" + "an" → ["advocate"] (1 token, filtered below).
        # Without this loop the probe over-emits "a case", "an advocate"
        # (RESEARCH §Pitfall 4).
        while words and words[0].lower() in LEAD_VERBS | COMMON_STOPS:
            words = words[1:]
        # Strip trailing prepositional / stop tokens ("Strategies for", "Voir Dire of").
        while words and words[-1].lower() in COMMON_STOPS:
            words = words[:-1]
        if len(words) < 2:
            continue
        # Emit the trailing 3-token AND 2-token forms (3 first; both are valid
        # candidates). Skip a slice whose FIRST word is itself a COMMON_STOP —
        # otherwise "Advocating a Case" would emit "a case" (RESEARCH §Pitfall 4
        # over-emission failure). The leading-LEAD_VERBS|stops strip above
        # handles the title's leading edge; this handles the slice's leading
        # edge.
        for n in (3, 2):
            if len(words) >= n:
                slice_ = words[-n:]
                if slice_[0].lower() in COMMON_STOPS:
                    continue
                phrase = " ".join(slice_).lower()
                out.append(ProbeCandidate(
                    section_ref=section_ref,
                    title=title,
                    phrase=phrase,
                    n_tokens=n,
                ))
    return out
