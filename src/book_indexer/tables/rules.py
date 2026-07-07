"""Rule extraction (PRIMARY path) — regex over text + chapter context.

Per RESEARCH §H-1, eyecite finds 0 procedural rule citations on the reference corpus
v1.0 while regex finds 226 FRE + 25 FRCP + 1 FRAP + 4 prose forms = 256
rule cites. This module is the LOAD-BEARING extractor for TAB-03 and is
deliberately eyecite-free (importing eyecite here would be misleading
dead code).

D-06: bare ``Rule N`` without an explicit prefix is resolved via
``fixtures/chapter_rule_systems.yaml``. The current chapter's
``rule_system`` determines which system bare-Rule-N maps to:

* ``rule_system: FRE``  → bare 'Rule N' → FRE N
* ``rule_system: FRCP`` → bare 'Rule N' → FRCP N
* ``rule_system: FRAP`` → bare 'Rule N' → FRAP N
* ``rule_system: MRPC`` → bare 'Rule N' → MRPC N (Plan 00 sign-off)
* ``rule_system: none`` → bare 'Rule N' → 'Rule (unspecified) N'
  (routes to the ``Rule`` pseudo-system per D-06)

Explicit prefixes (FRE/FRCP/FRAP/Fed.R./prose/MRPC) ALWAYS win over
chapter inference. Implementation: bare-Rule matches that overlap an
existing explicit hit are dropped via char-offset proximity.

D-05: subsection nesting (FRE 404(b) under FRE 404) is a presentation-
layer concern handled in Plan 03's ``__main__``. This module records
``subsection_path`` on each ``RawRuleHit``; IR construction does the
nesting.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .errors import ChapterRuleSystemError
from .regex_fallback import RawRuleHit, scan_rules

# Bare 'Rule N(...)' pattern. Case-sensitive on 'Rule' (manuscripts use
# Title-case). Must match a word boundary on either side so we don't
# accidentally match 'Rules' (the prose form lead-in).
# NOTE: trailing ``\b`` would prevent capturing the parenthetical
# subsection because ``)`` is a non-word character; use a positive
# leading boundary only.
_BARE_RULE_PATTERN = re.compile(
    r"\bRule\s+(?P<num>\d{1,4})(?P<subs>(?:\([a-z0-9]+\))*)"
)

# When a bare-Rule match falls within this many characters of an
# explicit-prefix hit's start offset, the bare match is dropped. The
# value 64 is chosen to cover the prose form
# ``Federal Rule of Civil Procedure 12`` whose start offset is up to
# ~32 chars before the inner ``Rule 12`` token plus a safety margin.
_BARE_DEDUP_DISTANCE = 64


def load_chapter_rule_systems(path: Path | None = None) -> dict[int, str]:
    """Load and validate the chapter→rule_system mapping.

    Per Wave 0 (Plan 03B-00) the fixture is hand-curated. If the file is
    missing OR has ``metadata.curated_by == 'PENDING_AUTHOR'``, this
    function raises ``ChapterRuleSystemError`` — Wave 0 is the gate.

    Returns a dict ``{chapter_number: rule_system}`` with integer keys.
    """
    if path is None:
        path = (
            Path(__file__).resolve().parents[3]
            / "fixtures"
            / "chapter_rule_systems.yaml"
        )
    if not path.exists():
        raise ChapterRuleSystemError(f"missing chapter_rule_systems fixture: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    metadata = data.get("metadata", {})
    if metadata.get("curated_by") == "PENDING_AUTHOR":
        raise ChapterRuleSystemError(
            f"{path} is still PENDING_AUTHOR — Wave 0 (Plan 03B-00) "
            "must complete before any rule extraction"
        )
    return {int(c["chapter"]): c["rule_system"] for c in data.get("chapters", [])}


def scan_rules_with_subsections(
    text: str,
    *,
    pdf_page: int,
    chapter: int,
    jurisdictions: list[str],
    chapter_rule_systems: dict[int, str],
) -> list[RawRuleHit]:
    """Scan ``text`` for all rule citations, applying chapter inference
    to bare ``Rule N`` references.

    This is the SINGLE entry point Plan 03 calls per chapter; it orchestrates
    ``regex_fallback.scan_rules`` for explicit prefixes and adds bare-Rule
    resolution on top.
    """
    if not text:
        return []

    # Step 1: explicit-prefix rules.
    explicit_hits = scan_rules(
        text, jurisdictions=jurisdictions, pdf_page=pdf_page
    )
    explicit_offsets = [h.char_offset for h in explicit_hits]

    # Step 2: bare 'Rule N'.
    chapter_system = chapter_rule_systems.get(chapter, "none")
    if chapter_system == "none":
        bare_target = "Rule"
    else:
        bare_target = chapter_system

    bare_hits: list[RawRuleHit] = []
    for m in _BARE_RULE_PATTERN.finditer(text):
        offset = m.start()
        # Drop bare match if it overlaps an explicit-prefix hit's region.
        # The explicit prefix 'Federal Rule of Civil Procedure 12' has
        # start offset ~32 chars before the inner 'Rule 12' word, so we
        # use _BARE_DEDUP_DISTANCE as the proximity threshold and require
        # the bare match to fall AFTER the explicit hit (since explicit
        # patterns include 'Rule' inside their span).
        overlapped = any(
            0 <= (offset - eo) <= _BARE_DEDUP_DISTANCE for eo in explicit_offsets
        )
        if overlapped:
            continue
        bare_hits.append(
            RawRuleHit(
                rule_system=bare_target,
                rule_number=int(m.group("num")),
                subsection_path=m.group("subs") or "",
                surface_form=m.group(),
                pdf_page=pdf_page,
                char_offset=offset,
                chapter_inferred=True,
            )
        )

    all_hits = explicit_hits + bare_hits
    all_hits.sort(key=lambda h: h.char_offset)
    return all_hits
