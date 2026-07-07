"""CUR-02 capitalization-fix application + strict letters-only guard.

Per Phase 7 CONTEXT D-05: recapitalize is exact-string replacement applied
to every string emitted by the Markdown/DOCX renderers in BOTH output
variants (subject-index canonicals, ``(also: …)`` variant lists, See / See
also targets, Table canonical_citation strings, synthesized parents).

Per CONTEXT Specifics line 599: pairs apply in the order they appear in the
fixture YAML; on conflict, the LATER pair wins (sequential ``str.replace``).

The strict guard (CUR-02 ship-blocker invariant): ``wrong.lower() ==
right.lower()`` for every pair. Any letter change (insertion, deletion, or
substitution beyond case) hard-fails the build via ``RecapitalizeGuardError``.
This prevents LLM hallucination from sneaking letter changes through under
a "capitalization fix" pretense.

requirements_addressed: CUR-02.
"""
from __future__ import annotations

from .errors import RecapitalizeGuardError


def assert_letters_only(pairs: list[tuple[str, str]]) -> None:
    """Raise ``RecapitalizeGuardError`` if any pair fails the strict guard.

    Strict guard: ``wrong.lower() == right.lower()``. Allows case mutation
    only (e.g., ``frcp → FRCP``); rejects any letter delta (e.g.,
    ``frcp → FROCP`` adds letter ``O``).

    Args:
        pairs: list of ``(wrong, right)`` tuples from the curator fixture.

    Raises:
        RecapitalizeGuardError: if any pair changes letters beyond case.
            Error message names the offending pair so the curator can fix
            the fixture and re-run.
    """
    for wrong, right in pairs:
        if wrong.lower() != right.lower():
            raise RecapitalizeGuardError(
                f"recapitalize strict_guard_violation: letters changed "
                f"between {wrong!r} and {right!r} (lower forms differ: "
                f"{wrong.lower()!r} != {right.lower()!r}). "
                f"CUR-02 only allows case mutation; reject this pair "
                f"in fixtures/index_curator_overrides.yaml."
            )


def apply_recap_pairs(text: str, pairs: list[tuple[str, str]]) -> str:
    """Apply each ``(wrong, right)`` pair to ``text`` via sequential replace.

    Pairs are applied in the order given (curator-fixture YAML order).
    Conflicts resolve "later wins" naturally — if pair A produces text that
    pair B replaces, B's substitution overrides A.

    Pure function — no validation here; the caller MUST run
    ``assert_letters_only(pairs)`` first to enforce the strict guard.

    Args:
        text: the rendered string under transformation.
        pairs: list of ``(wrong, right)`` tuples (validated; non-empty
            ``wrong`` strings).

    Returns:
        Text with every pair applied (idempotent on text containing none of
        the ``wrong`` substrings).
    """
    result = text
    for wrong, right in pairs:
        if not wrong:
            # Defensive: empty `wrong` would replace every position — skip.
            continue
        result = result.replace(wrong, right)
    return result
