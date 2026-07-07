"""Unit tests for assembly/dedup.py::_enrich_bucket_with_inflections (Phase 8 COV-03).

Source: 08-RESEARCH.md §Pattern 1 expected-behavior table.

Lock #1 preserved: the helper enriches ``bucket.variants`` only — it never
constructs an ``Evidence`` row (the AST static gate
``test_verify_is_sole_locator_source.py`` independently confirms).

UAT 08-1b extension (2026-05-01): the parametrized
``test_no_double_plural_artifacts_for_titlecase_plurals`` covers the
``inflect.plural`` Title-case-plural over-pluralization pitfall (e.g.
``Advocates`` → ``Advocateses``) — the fix gates the call with
``singular_noun(head)``.

requirements_addressed: COV-03.
"""
from __future__ import annotations

import inspect
import re

import pytest

import book_indexer.assembly.dedup as dedup_mod
from book_indexer.assembly.dedup import (
    BucketCandidate,
    _enrich_bucket_with_inflections,
)
from book_indexer.concepts.schema import ConceptCandidate


def _make_cand(term: str, canonical_form: str | None = None) -> ConceptCandidate:
    """Build a minimal ConceptCandidate for unit-level helper tests."""
    return ConceptCandidate(
        term=term,
        canonical_form=canonical_form if canonical_form is not None else term,
        variants=[],
        example_quote="placeholder example quote",
    )


def _make_bucket(canonical: str, surfaces: list[str]) -> BucketCandidate:
    """Build a fresh BucketCandidate seeded with ``surfaces``."""
    return BucketCandidate(
        lemma_key=canonical,
        surfaces=list(surfaces),
        variants=[],
    )


# ---------------------------------------------------------------------------
# Behavior tests — RESEARCH §Pattern 1 expected-behavior table.
# ---------------------------------------------------------------------------


def test_enrich_adds_term_when_distinct_from_canonical() -> None:
    """The smoking-gun case: cand.term plural lands in variants when canonical is singular."""
    bucket = _make_bucket("special interrogatory", ["special interrogatory"])
    cand = _make_cand(term="Special Interrogatories", canonical_form="special interrogatory")

    _enrich_bucket_with_inflections(bucket, cand, "special interrogatory")

    # cand.term (the verbatim plural body-form) MUST appear in variants.
    assert "Special Interrogatories" in bucket.variants


def test_enrich_adds_plural_inflection() -> None:
    """When canonical is singular, plural inflection of the head lands in variants."""
    bucket = _make_bucket("alternate juror", ["alternate juror"])
    cand = _make_cand("alternate juror")

    _enrich_bucket_with_inflections(bucket, cand, "alternate juror")

    assert "alternate jurors" in bucket.variants


def test_enrich_adds_singular_inflection_when_canonical_is_plural() -> None:
    """When canonical is plural, singular inflection of the head lands in variants."""
    bucket = _make_bucket("special interrogatories", ["special interrogatories"])
    cand = _make_cand("special interrogatories")

    _enrich_bucket_with_inflections(bucket, cand, "special interrogatories")

    assert "special interrogatory" in bucket.variants


def test_enrich_does_not_pollute_surfaces() -> None:
    """RESEARCH §Pitfall 3: surfaces drives canonical election; the helper must NEVER
    mutate bucket.surfaces — only bucket.variants."""
    bucket = _make_bucket("voir dire", ["voir dire"])
    cand = _make_cand(term="Voir Dire Examinations", canonical_form="voir dire")

    surfaces_before = list(bucket.surfaces)
    _enrich_bucket_with_inflections(bucket, cand, "voir dire")

    # surfaces is unchanged — the seed list is the only entry.
    assert bucket.surfaces == surfaces_before


def test_no_duplicate_when_term_already_in_surfaces() -> None:
    """If cand.term already equals a surface, must not double-append into variants."""
    bucket = _make_bucket("voir dire", ["voir dire"])
    cand = _make_cand("voir dire")

    _enrich_bucket_with_inflections(bucket, cand, "voir dire")

    # variants should not contain "voir dire" — it's already in surfaces.
    assert bucket.variants.count("voir dire") == 0


def test_empty_term_short_circuits_cleanly() -> None:
    """An empty cand.term must not raise and must not insert empty strings.

    ConceptCandidate enforces ``min_length=2`` on ``term``, so we use a
    synthetic candidate-like object that bypasses the schema (the helper
    guards on truthiness, not on schema).
    """

    class _SyntheticCand:
        term = ""
        canonical_form = "x"

    bucket = _make_bucket("x", ["x"])
    # Should not raise.
    _enrich_bucket_with_inflections(bucket, _SyntheticCand(), "x")
    # No empty strings smuggled into variants.
    assert "" not in bucket.variants


def test_no_double_pluralization_artifacts() -> None:
    """``inflect.plural`` flips between singular and plural — confirm we never
    produce ``interrogatorieses``-style double-plural artifacts on already-
    plural input."""
    bucket = _make_bucket("interrogatories", ["interrogatories"])
    cand = _make_cand("interrogatories")

    _enrich_bucket_with_inflections(bucket, cand, "interrogatories")

    # No double-plural artifacts in any form.
    for v in bucket.variants:
        assert "ieses" not in v, f"double-plural artifact in variants: {v!r}"
        assert "esies" not in v, f"double-plural artifact in variants: {v!r}"


def test_no_false_string_from_singular_noun() -> None:
    """``inflect.singular_noun`` returns Python ``False`` for already-singular
    input. The fallback to the head word ensures the literal string
    ``"False"`` and the boolean ``False`` never reach variants."""
    bucket = _make_bucket("damages", ["damages"])
    cand = _make_cand("damages")

    _enrich_bucket_with_inflections(bucket, cand, "damages")

    assert "False" not in bucket.variants
    assert False not in bucket.variants  # noqa: E712 — explicit Falseness check


def test_lock_1_no_evidence_construction_in_dedup_module() -> None:
    """Lock #1 sanity scan: the dedup module must not construct an Evidence row.

    This is a smoke test, not a substitute for the full AST static gate at
    ``tests/invariants/test_verify_is_sole_locator_source.py`` — but failing
    here surfaces a Lock #1 regression in the most obvious place.
    """
    src = inspect.getsource(dedup_mod)
    # Reject any literal ``Evidence(`` constructor call.
    assert "Evidence(" not in src, (
        "Lock #1 violation: src/book_indexer/assembly/dedup.py constructs "
        "an Evidence row (look for `Evidence(...)`). The sole locator-"
        "emitter must remain ``book_indexer.verify.verify``."
    )
    # Reject any direct verify-module import (Phase 4 reads Evidence rows
    # through verifier_sweep — dedup.py is purely structural).
    assert "from book_indexer.verify import" not in src, (
        "Lock #1 boundary: dedup.py imported the verify module directly; "
        "Phase 4 reads Evidence rows only via assembly/verifier_sweep.py."
    )


def test_inflect_engine_is_module_level() -> None:
    """Pinned per-module ``inflect.engine()`` — recreating per call is a hot-
    loop hazard on 1000+ candidates."""
    assert hasattr(dedup_mod, "_INFLECT"), (
        "dedup module must expose a module-level ``_INFLECT`` engine; per-"
        "call instantiation would slow ``build_buckets`` on 1000+ candidates."
    )


# ---------------------------------------------------------------------------
# UAT 08-1b regression — Title-case-plural over-pluralization rejection.
#
# Empirically: ``_INFLECT.plural("Advocates") == "Advocateses"``. The
# pre-UAT helper was passing Title-case plural surfaces (cand.term values
# like "Advocates", "Pictures", "Similarities") through ``_INFLECT.plural``
# without checking plurality first, contaminating ~50+ rendered entries
# with ``*eses`` artifacts. Post-fix: the head is classified by
# ``singular_noun`` first, and only the singular form (or the input itself
# if singular) is fed to ``_INFLECT.plural``.
# ---------------------------------------------------------------------------


# Confirmed offenders observed in artifacts/render/index.md before the fix:
# advocate / similarity / picture / source / anecdote / bias / chance /
# conference / circumstance / damage / defense / deficiency / expense /
# inconsistency. We test BOTH casings (lowercase plural and Title-case
# plural) — pre-fix, only the Title-case path leaked because lowercase
# input was correctly recognized as plural by ``_INFLECT.plural``.
_DOUBLE_PLURAL_OFFENDERS: list[tuple[str, str]] = [
    ("advocates", "Advocates"),
    ("similarities", "Similarities"),
    ("pictures", "Pictures"),
    ("sources", "Sources"),
    ("anecdotes", "Anecdotes"),
    ("biases", "Biases"),
    ("chances", "Chances"),
    ("conferences", "Conferences"),
    ("circumstances", "Circumstances"),
    ("damages", "Damages"),
    ("defenses", "Defenses"),
    ("deficiencies", "Deficiencies"),
    ("expenses", "Expenses"),
    ("inconsistencies", "Inconsistencies"),
]

_DOUBLE_PLURAL_RE = re.compile(r"[a-z]eses\b", re.IGNORECASE)


@pytest.mark.parametrize(
    ("lower_plural", "title_plural"),
    _DOUBLE_PLURAL_OFFENDERS,
    ids=[p[1] for p in _DOUBLE_PLURAL_OFFENDERS],
)
def test_no_double_plural_artifacts_for_titlecase_plurals(
    lower_plural: str, title_plural: str
) -> None:
    """UAT 08-1b: Title-case plural surfaces must NOT generate ``*eses``
    over-pluralization artifacts in bucket.variants.

    Pre-fix: ``_INFLECT.plural("Advocates") == "Advocateses"`` and the
    helper would append the ``*eses`` form to ``bucket.variants``.
    Post-fix: ``singular_noun("Advocates") == "Advocate"`` is checked
    first — head is already plural, so ``plural_head`` reuses the
    Title-case input and ``sing_head`` becomes ``"Advocate"``; no
    ``*eses`` artifact is generated.

    Both Title-case and lowercase plural forms are tested. The fix must
    leave the lowercase path GREEN (it always was) and additionally
    eliminate the ``*eses`` form on the Title-case path.
    """
    for plural_surface in (title_plural, lower_plural):
        bucket = _make_bucket(plural_surface, [plural_surface])
        cand = _make_cand(plural_surface)
        _enrich_bucket_with_inflections(bucket, cand, plural_surface)

        # Hard rule: no ``*eses`` artifacts allowed in any variant.
        for v in bucket.variants:
            assert not _DOUBLE_PLURAL_RE.search(v), (
                f"double-plural artifact in bucket.variants for surface "
                f"{plural_surface!r}: variant {v!r} matches the ``*eses`` "
                f"over-pluralization pattern (UAT 08-1b regression)."
            )
        # Also: the specific contaminating form must not appear. We compute
        # what the WRONG path would produce (head + 'es') and confirm the
        # variants list does not contain it.
        bad_form = plural_surface + "es"
        assert bad_form not in bucket.variants, (
            f"bucket.variants contained the over-pluralized {bad_form!r} "
            f"for surface {plural_surface!r}; the singular_noun guard "
            f"should have prevented this."
        )


def test_helper_is_idempotent_under_repeat_invocation() -> None:
    """Running the helper twice on the same ``(bucket, cand, surface)`` yields
    the same variants list — guards against any non-determinism."""
    bucket = _make_bucket("alternate juror", ["alternate juror"])
    cand = _make_cand("alternate juror")

    _enrich_bucket_with_inflections(bucket, cand, "alternate juror")
    after_first = list(bucket.variants)

    _enrich_bucket_with_inflections(bucket, cand, "alternate juror")
    after_second = list(bucket.variants)

    assert after_first == after_second, (
        "helper produced different variants on repeat invocation: "
        f"{after_first!r} vs {after_second!r}"
    )
