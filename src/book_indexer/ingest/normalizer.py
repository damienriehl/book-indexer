"""Unicode canonical normalization (D-15, ING-06, ING-07).

Two entry points:
  - normalize(s)          -> lowercased `norm` form; ligatures expanded, quotes
                              canonical, soft-hyphens / ZWSP / BOM stripped, whitespace
                              collapsed. Used for FTS5 index + match surface.
  - canonicalize_text(s)  -> `text` form; preserves ligatures, smart quotes, dashes,
                              NBSP. Only strips invisible-useless codepoints
                              (U+00AD soft hyphen, U+200B ZWSP, U+FEFF BOM).
                              Used for evidence snippets humans read.
"""
from __future__ import annotations

import re

LIGATURE_MAP: dict[str, str] = {
    "ﬁ": "fi",   # U+FB01 ﬁ
    "ﬂ": "fl",   # U+FB02 ﬂ
    "ﬃ": "ffi",  # U+FB03 ﬃ
    "ﬄ": "ffl",  # U+FB04 ﬄ
    "ﬅ": "ft",   # U+FB05 ﬅ
    "ﬆ": "st",   # U+FB06 ﬆ
}
QUOTE_MAP: dict[str, str] = {
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
}
DASH_MAP: dict[str, str] = {
    "–": "-",    # en-dash
    "—": "--",   # em-dash
}
WS_MAP: dict[str, str] = {
    " ": " ",   # NBSP -> regular space
    "​": "",    # zero-width space stripped
    "﻿": "",    # BOM / zero-width no-break space stripped
}
STRIP_MAP: dict[str, str] = {
    "­": "",    # soft hyphen stripped
}
ELLIPSIS_MAP: dict[str, str] = {
    "…": "...",
}

# Merge all substitutions for a single pass in normalize().
_NORM_ALL: dict[str, str] = {
    **LIGATURE_MAP,
    **QUOTE_MAP,
    **DASH_MAP,
    **WS_MAP,
    **STRIP_MAP,
    **ELLIPSIS_MAP,
}

# For canonicalize_text: only strip the truly invisible useless codepoints.
_TEXT_STRIP: set[str] = {"­", "​", "﻿"}

_WS_RE = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Produce the canonical `norm` form for matching and FTS5 indexing."""
    out = "".join(_NORM_ALL.get(ch, ch) for ch in s)
    return _WS_RE.sub(" ", out).strip().lower()


def canonicalize_text(s: str) -> str:
    """Produce the user-facing `text` form preserving visible characters."""
    return "".join("" if ch in _TEXT_STRIP else ch for ch in s)
