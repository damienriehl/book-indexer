"""Hyphen rejoin policy (D-14 line-end, D-16 cross-page).

Two entry points serve Plan 01-04's tokenizer:

- :func:`is_rejoin_valid` — lexical-validity check (D-14). Rejoin a line-end
  hyphen's two halves ONLY when the concatenated (lowercased) token is either
  in spaCy's English vocabulary (``Lexeme.is_oov == False``) or in the
  caller-supplied override set (normalized lowercased forms). Genuine
  hyphenated compounds like ``cross-examination`` are preserved verbatim
  because ``crossexamination`` is OOV.

- :func:`should_rejoin_line_end` — precondition detector. Inspects a line's
  trailing content and returns ``(True, stem)`` iff the line ends with a
  letter immediately followed by ``-``. The *stem* is the word preceding the
  hyphen; the continuation is the first word of the next line. Callers combine
  this with :func:`is_rejoin_valid` to decide whether to glue.

Cross-page hyphen rejoin (D-16): the tokenizer consumes the last-block-of-page-N
and the first-block-of-page-N+1; the rejoined token is credited to page N with
``crosses_page_break=1`` (``verify()`` inherits the rule that a span citation
names the *starting* page, per ARCHITECTURE.md §2).

No spaCy import here — typing is guarded via :class:`typing.TYPE_CHECKING` so
this module stays cheap to import (the vocabulary lookup is the only API we
exercise, and callers pass in a pre-loaded ``nlp``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import spacy.language


def is_rejoin_valid(
    left_stem: str,
    right_continuation: str,
    nlp: "spacy.language.Language",
    overrides: set[str],
) -> bool:
    """Return True iff concatenating ``left_stem`` + ``right_continuation``
    yields a token the corpus should treat as a single word (D-14).

    Check order:
      1. Lowercased concatenation appears in ``overrides`` → rejoin.
      2. spaCy ``Lexeme.is_oov == False`` for the concatenation → rejoin.
      3. Otherwise → do NOT rejoin (preserve the hyphen verbatim).

    The ``overrides`` set lets callers extend vocabulary with domain terms
    whose concatenated form wouldn't appear in ``en_core_web_lg`` but which
    we still want to treat as one token (e.g., legal Latin phrases with no
    space in the joined form).
    """
    candidate = (left_stem + right_continuation).lower()
    if not candidate:
        return False
    if candidate in overrides:
        return True
    lex = nlp.vocab[candidate]
    return not lex.is_oov


def should_rejoin_line_end(
    line_text: str, next_line_first_word: str
) -> tuple[bool, str | None]:
    """Return ``(should_check, stem_if_hyphen_else_None)``.

    The ``next_line_first_word`` is accepted for symmetry but not required for
    the hyphen-presence check — callers typically already hold it so we pass
    it through the API contract rather than requiring them to split twice.

    Heuristic: if ``line_text`` (right-stripped) ends with ``-`` and at least
    one alphabetic character precedes the hyphen, return ``(True, stem)``
    where ``stem`` is the whitespace-separated word immediately before the
    hyphen (minus the hyphen itself). Otherwise ``(False, None)``.
    """
    del next_line_first_word  # part of the contract; unused in the heuristic
    stripped = line_text.rstrip()
    if len(stripped) < 2 or not stripped.endswith("-"):
        return (False, None)
    if not stripped[-2].isalpha():
        return (False, None)
    # Stem is the last whitespace-separated token sans trailing hyphen.
    before_hyphen = stripped[:-1]
    parts = before_hyphen.split()
    if not parts:
        return (False, None)
    stem = parts[-1]
    if not stem:
        return (False, None)
    return (True, stem)
