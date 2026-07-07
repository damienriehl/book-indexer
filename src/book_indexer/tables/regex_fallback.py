"""Regex fallback for rule extraction (PRIMARY path) + Constitution.

Per RESEARCH §H-1, eyecite finds 0 procedural rule citations on the reference corpus
v1.0 while regex finds 226 FRE + 25 FRCP + 1 FRAP + 4 prose-form hits.
This module is the LOAD-BEARING extractor for procedural rules.

Pattern catalog (D-10 + Plan 00 author sign-off + RESEARCH §H-10):

* ``FRE_PATTERN``       — ``\\bFRE\\s+N(\\(...\\))*``    (case-sensitive)
* ``FRCP_PATTERN``      — ``\\bFRCP\\s+N(\\(...\\))*``   (case-sensitive)
* ``FRAP_PATTERN``      — ``\\bFRAP\\s+N(\\(...\\))*``   (case-sensitive)
* ``FEDR_PATTERN``      — ``\\bFed\\.R\\.{Civ|Crim|App|Bankr|Evid}\\.P\\.``
* ``PROSE_RULE_PATTERN`` — ``Federal Rules? of {Evidence|Civil Procedure|
                            Appellate Procedure} N``
* ``MRPC_PATTERN``      — ``(Model Rule|MRPC|M.R.P.C.) N(.NN)*(\\(...\\))*``
* ``AMENDMENT_PATTERN`` — ``First|...|Twenty-seventh Amendment``
* ``US_CONST_ART_PATTERN`` — ``U.S. Const. art. R(, § N)?``

This module is **pure regex** — it does NOT import eyecite, does NOT call
``verify()``, and does NOT construct Pydantic IR. Plan 03's
``verifier_bridge`` consumes the ``RawRuleHit`` dataclass and routes
verification + IR construction.

``RawRuleHit`` lives canonically here (consumed by ``rules.py`` and
``verifier_bridge``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Compiled regex patterns (module-import time — no lazy compilation).
# ---------------------------------------------------------------------------

# D-10 abbreviated. Capitalization is by convention (FRE/FRCP/FRAP are
# upper-case in legal writing); case-sensitive on purpose.
FRE_PATTERN = re.compile(
    r"\bFRE\s+(?P<num>\d{1,4})(?P<subs>(?:\([a-z0-9]+\))*)"
)
FRCP_PATTERN = re.compile(
    r"\bFRCP\s+(?P<num>\d{1,4})(?P<subs>(?:\([a-z0-9]+\))*)"
)
FRAP_PATTERN = re.compile(
    r"\bFRAP\s+(?P<num>\d{1,4})(?P<subs>(?:\([a-z0-9]+\))*)"
)

# D-10 Fed. R. {sys}. P. — handle optional whitespace and the dot-collapsed
# form ``Fed.R.Civ.P.`` as well as the spaced form ``Fed. R. Civ. P.``.
FEDR_PATTERN = re.compile(
    r"\bFed\.\s*R\.\s*(?P<sys>Civ|Crim|App|Bankr|Evid)\.\s*P\.\s+"
    r"(?P<num>\d{1,4})(?P<subs>(?:\([a-z0-9]+\))*)"
)

# D-10 prose. Allows ``Rule`` or ``Rules`` and arbitrary whitespace between
# tokens (manuscripts often re-flow whitespace across line breaks).
PROSE_RULE_PATTERN = re.compile(
    r"\bFederal\s+Rules?\s+of\s+"
    r"(?P<sys>Evidence|Civil\s+Procedure|Appellate\s+Procedure)\s+"
    r"(?P<num>\d{1,4})(?P<subs>(?:\([a-z0-9]+\))*)",
    flags=re.IGNORECASE,
)

# MRPC — added per Plan 00 author sign-off (chapter 1 = Model Rules of
# Professional Conduct). Captures the prefix and the dotted-numeric path
# (``3.3``, ``3.4(c)``, ``8.4(a)(2)``). The ``num`` group is the integer
# part; the ``subs`` group is the rest (dotted suffix + parentheticals).
MRPC_PATTERN = re.compile(
    r"\b(?:Model\s+Rule|MRPC|M\.R\.P\.C\.)\s+"
    r"(?P<num>\d{1,4})(?P<subs>(?:\.\d+)*(?:\([a-z0-9]+\))*)"
)

# Constitution — RESEARCH §H-10. Ordinals First..Twenty-seventh.
AMENDMENT_PATTERN = re.compile(
    r"\b(?P<ordinal>First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|"
    r"Ninth|Tenth|Eleventh|Twelfth|Thirteenth|Fourteenth|Fifteenth|"
    r"Sixteenth|Seventeenth|Eighteenth|Nineteenth|Twentieth|"
    r"Twenty-first|Twenty-second|Twenty-third|Twenty-fourth|"
    r"Twenty-fifth|Twenty-sixth|Twenty-seventh)\s+Amendment\b"
)

US_CONST_ART_PATTERN = re.compile(
    r"\bU\.?S\.?\s+Const(?:itution)?(?:\.|,)?\s+art\.?\s+"
    r"(?P<article>[IVX]+)"
    r"(?:\s*,\s*§\s*(?P<section>\d+))?"
)


# ---------------------------------------------------------------------------
# Raw record — canonical home for RawRuleHit.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawRuleHit:
    """A single raw rule citation found in source text.

    Pure data — no Pydantic, no validation. Plan 03's ``verifier_bridge``
    wraps this into a ``RuleEntry`` after ``verify()`` succeeds.

    ``rule_system`` is one of ``FRE`` / ``FRCP`` / ``FRAP`` / ``FedR`` /
    ``MRPC`` / ``Rule`` (last is the unspecified pseudo-system per D-06).

    ``subsection_path`` is the parenthetical path (e.g., ``(b)`` or
    ``(b)(1)``) for FRE/FRCP/FRAP/Fed.R., or the dotted-path-plus-parens
    (e.g., ``.3(a)``) for MRPC. Empty string for bare-parent hits.

    ``chapter_inferred`` is True only when the rule_system was inferred
    from the chapter rule_systems mapping (set by ``rules.py``); regex
    matches with explicit prefixes always have ``chapter_inferred=False``.
    """

    rule_system: str
    rule_number: int
    subsection_path: str
    surface_form: str
    pdf_page: int
    char_offset: int
    chapter_inferred: bool


# ---------------------------------------------------------------------------
# Public scan functions.
# ---------------------------------------------------------------------------


# Fed.R. system suffix → canonical rule_system code.
_FEDR_SYS_MAP = {
    "Civ": "FRCP",
    "Crim": "FedR",  # FedRCrimP — future-proof; not in v1.0 corpus.
    "App": "FRAP",
    "Bankr": "FedR",  # FedRBankP — future-proof.
    "Evid": "FRE",
}

# Prose system → canonical rule_system code. Whitespace-collapsed lookup.
_PROSE_SYS_MAP = {
    "evidence": "FRE",
    "civil procedure": "FRCP",
    "appellate procedure": "FRAP",
}


def scan_rules(
    text: str,
    *,
    jurisdictions: list[str],
    pdf_page: int,
) -> list[RawRuleHit]:
    """Scan ``text`` for explicit-prefix rule citations.

    Always-on patterns (federal, gated by ``us`` ∈ ``jurisdictions``):
    FRE / FRCP / FRAP / Fed.R. / prose / MRPC.

    State-rule patterns are gated by per-state codes in ``jurisdictions``.
    the reference corpus has ``jurisdictions=['us']`` so no state patterns fire.
    Adding a state requires a CONTEXT amendment + a new pattern here.

    Returns hits sorted by ``char_offset`` ascending.
    """
    if not text:
        return []

    hits: list[RawRuleHit] = []
    has_us = "us" in jurisdictions

    if has_us:
        for pat, sys_name in (
            (FRE_PATTERN, "FRE"),
            (FRCP_PATTERN, "FRCP"),
            (FRAP_PATTERN, "FRAP"),
        ):
            for m in pat.finditer(text):
                hits.append(
                    RawRuleHit(
                        rule_system=sys_name,
                        rule_number=int(m.group("num")),
                        subsection_path=m.group("subs") or "",
                        surface_form=m.group(),
                        pdf_page=pdf_page,
                        char_offset=m.start(),
                        chapter_inferred=False,
                    )
                )

        # Fed. R. {sys}. P. expansion.
        for m in FEDR_PATTERN.finditer(text):
            sys_name = _FEDR_SYS_MAP[m.group("sys")]
            hits.append(
                RawRuleHit(
                    rule_system=sys_name,
                    rule_number=int(m.group("num")),
                    subsection_path=m.group("subs") or "",
                    surface_form=m.group(),
                    pdf_page=pdf_page,
                    char_offset=m.start(),
                    chapter_inferred=False,
                )
            )

        # Prose forms.
        for m in PROSE_RULE_PATTERN.finditer(text):
            sys_text = re.sub(r"\s+", " ", m.group("sys").strip().lower())
            sys_name = _PROSE_SYS_MAP.get(sys_text, "FRCP")
            hits.append(
                RawRuleHit(
                    rule_system=sys_name,
                    rule_number=int(m.group("num")),
                    subsection_path=m.group("subs") or "",
                    surface_form=m.group(),
                    pdf_page=pdf_page,
                    char_offset=m.start(),
                    chapter_inferred=False,
                )
            )

        # MRPC — Plan 00 author sign-off.
        for m in MRPC_PATTERN.finditer(text):
            hits.append(
                RawRuleHit(
                    rule_system="MRPC",
                    rule_number=int(m.group("num")),
                    subsection_path=m.group("subs") or "",
                    surface_form=m.group(),
                    pdf_page=pdf_page,
                    char_offset=m.start(),
                    chapter_inferred=False,
                )
            )

    # State-rule patterns plug in here, gated on each state code in
    # ``jurisdictions`` (e.g., 'nj' → N.J.R.E.). the reference corpus has none.

    hits.sort(key=lambda h: h.char_offset)
    return hits


def scan_constitution(text: str, *, pdf_page: int) -> list[dict]:
    """Scan ``text`` for U.S. Constitution citations.

    Returns a list of dicts (NOT RawRuleHits) since Plan 03's
    ``statutes.py`` consumes them and wraps each into a
    ``RawStatuteHit``. Each dict has keys ``kind``, ``display_name``,
    ``surface_form``, ``pdf_page``, ``char_offset``.

    Sorted by ``char_offset`` ascending.
    """
    if not text:
        return []

    hits: list[dict] = []

    for m in AMENDMENT_PATTERN.finditer(text):
        hits.append(
            {
                "kind": "amendment",
                "display_name": f"{m.group('ordinal')} Amendment",
                "surface_form": m.group(),
                "pdf_page": pdf_page,
                "char_offset": m.start(),
            }
        )

    for m in US_CONST_ART_PATTERN.finditer(text):
        sec = m.group("section")
        display = f"U.S. Const. art. {m.group('article')}"
        if sec:
            display = f"{display}, § {sec}"
        hits.append(
            {
                "kind": "article",
                "display_name": display,
                "surface_form": m.group(),
                "pdf_page": pdf_page,
                "char_offset": m.start(),
            }
        )

    hits.sort(key=lambda h: h["char_offset"])
    return hits
