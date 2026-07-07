"""D-01 alphabetization — sort key for the Table of Cases.

Sort key is the first named party with a curated set of leading prefixes
stripped. Display name is unchanged; only sort order is affected.

Strip set is locked per CONTEXT.md D-01. Adding a prefix requires a
CONTEXT amendment — DO NOT extend ``STRIP_SET`` silently in code review.

Reference: 03B-CONTEXT.md D-01; 03B-RESEARCH.md §H-4.

requirements_addressed: TAB-01.
"""
from __future__ import annotations

# Locked per D-01. Each entry's trailing space is included so a
# case-insensitive ``startswith`` is sufficient (no need for a separate
# trailing-context regex). Adding to this list = CONTEXT amendment.
#
# A tuple (not a list) because tuples are immutable: any attempt to
# ``STRIP_SET[0] = "foo"`` raises TypeError, locking the contract at
# runtime. The accompanying ``test_strip_set_size_locked`` test traps
# any silent expansion at code-review time.
STRIP_SET: tuple[str, ...] = (
    "In re ",
    "Ex parte ",
    "Matter of ",
    "Estate of ",
    "United States v. ",
    "State v. ",
    "People v. ",
    "Commonwealth v. ",
)


def sort_key(display_name: str) -> str:
    """Return the D-01 sort key for a case display name.

    The display name is unchanged in storage; this function returns ONLY
    the alphabetization key. For ``"In re Smith"`` returns ``"Smith"``.
    For ``"Smith v. United States"`` (US is appellee) returns the input
    unchanged.

    Case-insensitive prefix match. Strip happens only when the full
    prefix (including trailing space) matches.
    """
    if not display_name:
        return display_name
    lower = display_name.lower()
    for prefix in STRIP_SET:
        if lower.startswith(prefix.lower()):
            return display_name[len(prefix):].lstrip()
    return display_name
