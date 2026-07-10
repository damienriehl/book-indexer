"""Phase 9 — Editorial overrides apply pass (7th projection pass).

Pure-functional render-time projection that consumes a curator-signed
``EditorialOverrides`` fixture and projects ``list[IndexEntry]`` through
the 9 R-class rule pipeline in the locked D-01 order:

    R4 → R5 → R6 → R7 → R3 → R8 → R2 → R1 → R9

R1–R8 mutate the IR projection (entries / variants / canonicals / locators);
R9 is text-only — handled by ``apply_r9_whitespace`` at the renderer's
post-emit boundary (mirrors Phase 7's ``apply_recap_pairs`` shape;
``markdown.py:670-673``).

R5 (delete cross-ref) and R6 (suppress synthesized parent) are IR-NO-OPS
in this pass; they emit side-channel sets (``xref_removal_set`` /
``synth_suppressed_stems``) that the renderer consumes when filtering
``derive_cross_refs(...)`` output and the synthetics list, respectively.

Architecture locks honored:

  Lock #1 — this module NEVER imports ``verify`` and NEVER constructs
    ``Evidence(...)``. AST gate ``test_no_evidence_construction_outside_verify``
    runs over every ``.py`` under ``src/book_indexer/`` outside
    ``verify/``; a hand-grep of this file for ``Evidence(`` returns empty
    by construction.
  Lock #2 — no locator-shaped fields on any new dataclass. ``Mismatch``,
    ``ApplyPassResult``, and the function signatures expose only canonical
    strings, rule-class names, and side-channel frozensets of strings.
  Lock #3 — no ``anthropic`` / ``claude_agent_sdk`` imports.
  Lock #5 — pure-functional, sorted iteration where ordering matters,
    ``@dataclass(frozen=True)`` for results. Byte-identity by-construction.

Two-phase idempotence (D-02):

  - First "real" apply uses ``allow_stale=False``. Every fixture entry MUST
    match its ``before`` target in the IR or ``EditorialOverrideMismatch``
    raises with the full mismatch list (Lock #5 protection against Phase 4
    canonical drift).
  - Re-apply (or any later apply) uses ``allow_stale=True``. Missing-target
    rules are silent no-ops via the per-R-class ``_already_applied_R{N}``
    predicate; the result is byte-identical to the first apply.

requirements_addressed: REND-02 (apply pass), REND-04 (4-emitter parity at
IR-projection level), REND-05 (byte-identity gate consumes this output),
REND-06 (``EditorialOverrideMismatch`` halts on stale ``before``).
"""
from __future__ import annotations

import difflib
import sys
from dataclasses import dataclass
from pathlib import Path

from book_indexer.curator.fixture import (
    EditorialOverrides,
    R1StripVariantsRule,
    R2RecapitalizeRule,
    R3RewordRule,
    R4DeleteEntryRule,
    R5DeleteXrefRule,
    R6PromoteSingleChildRule,
    R7FoldDoubledWordRule,
    R8PluralCanonicalRule,
    R9WhitespaceRule,
)
from book_indexer.tables.ir import Locator

from .ir import IndexEntry

__all__ = [
    "ApplyPassResult",
    "EditorialOverrideMismatch",
    "Mismatch",
    "_APPLY_ORDER",
    "apply_editorial_overrides",
    "apply_r9_whitespace",
    "write_mismatch_report",
]


# --------------------------------------------------------------------------
# D-01 locked apply order — compile-time tuple. NEVER reorder without
# updating CONTEXT.md D-01 and the regression test
# `tests/unit/render/test_apply_order_locked.py`.
# --------------------------------------------------------------------------


_APPLY_ORDER: tuple[str, ...] = (
    "R4_delete_entry",
    "R5_delete_xref",
    "R6_promote_single_child",
    "R7_fold_doubled_word",
    "R3_reword",
    "R8_plural_canonical",
    "R2_recapitalize",
    "R1_strip_variants",
    "R9_whitespace",
)


# --------------------------------------------------------------------------
# Mismatch + exception
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Mismatch:
    """One stale-fixture mismatch, formatted per CONTEXT D-08.

    Fields are all strings or string tuples — no locator-shaped data
    crosses this boundary (Lock #2 by construction).

    Fields:
        rule_class: D-01 R-section name (e.g. ``"R3_reword"``).
        fixture_entry_text: short human-readable rule summary (e.g.
            ``"R3RewordRule(before='good evidence rule', after='best evidence rule')"``).
        expected_before_canonical: the IR canonical the fixture rule
            expected to find (its ``before`` / ``term`` / ``wrong`` / etc.).
        closest_ir_canonicals: top-3 fuzzy matches from the IR (difflib
            cutoff 0.6) — the curator's triage hints.
        suggested_action: one of the three D-08 templates:
            ``"update fixture: rename {expected!r} → {closest[0]!r}"``,
            ``"delete fixture entry: target removed upstream
            (no IR canonical within edit-distance 0.6 of {expected!r})"``,
            or a custom string for ambiguous / structural cases (e.g.
            R3 ``"ambiguous: ..."``).
    """

    rule_class: str
    fixture_entry_text: str
    expected_before_canonical: str
    closest_ir_canonicals: tuple[str, ...]
    suggested_action: str

    def format(self) -> str:
        """D-08 markdown shape, byte-deterministic."""
        return (
            f"## {self.rule_class} mismatch\n"
            f"fixture_entry_text: {self.fixture_entry_text}\n"
            f"expected_before_canonical: {self.expected_before_canonical}\n"
            f"closest_ir_canonicals: "
            f"{list(self.closest_ir_canonicals)}\n"
            f"suggested_action: {self.suggested_action}\n"
        )


class EditorialOverrideMismatch(Exception):
    """Raised by ``apply_editorial_overrides`` on the first apply when one
    or more fixture entries fail to match their IR target AND
    ``allow_stale=False``. The full mismatch list is available via
    ``self.mismatches`` for triage (D-08).
    """

    def __init__(self, mismatches: list[Mismatch]) -> None:
        self.mismatches: tuple[Mismatch, ...] = tuple(mismatches)
        body = "\n".join(m.format() for m in self.mismatches)
        super().__init__(
            f"editorial-overrides apply pass found "
            f"{len(self.mismatches)} stale fixture entr"
            f"{'y' if len(self.mismatches) == 1 else 'ies'} "
            f"(set ALLOW_STALE_OVERRIDES=1 to skip):\n\n{body}"
        )


# --------------------------------------------------------------------------
# Result dataclass (frozen — Lock #5 by-construction)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplyPassResult:
    """Output of ``apply_editorial_overrides``.

    Fields:
        entries: the projected ``list[IndexEntry]`` after R1/R2/R3/R4/R7/R8
            applied. R5/R6/R9 do NOT touch ``entries``; they emit
            side-channels.
        mismatches: tuple of ``Mismatch`` records for stale fixture entries.
            Empty on a clean apply. ALWAYS populated regardless of
            ``allow_stale`` — the renderer writes them to the audit report
            (D-08).
        xref_removal_set: R5 side-channel — heads the renderer must filter
            out of ``derive_cross_refs`` output.
        synth_suppressed_stems: R6 side-channel — synthesized parent stems
            the renderer must omit from the merged stream.
    """

    entries: list[IndexEntry]
    mismatches: tuple[Mismatch, ...]
    xref_removal_set: frozenset[str]
    synth_suppressed_stems: frozenset[str]


# --------------------------------------------------------------------------
# Mismatch helper — fuzzy-match suggestion (D-08 wording)
# --------------------------------------------------------------------------


def _mismatch(
    rule_class: str,
    fixture_text: str,
    expected: str,
    canonicals: list[str],
    *,
    suggested: str | None = None,
) -> Mismatch:
    """Build a Mismatch with difflib top-3 fuzzy-match suggestions.

    When ``suggested`` is None, picks the canonical D-08 template:

      - empty `closest` → "delete fixture entry: target removed upstream
        (no IR canonical within edit-distance 0.6 of {expected!r})"
      - non-empty       → "update fixture: rename {expected!r} → {closest[0]!r}"
    """
    closest: tuple[str, ...] = tuple(
        difflib.get_close_matches(expected, canonicals, n=3, cutoff=0.6)
    )
    if suggested is None:
        if not closest:
            suggested = (
                f"delete fixture entry: target removed upstream "
                f"(no IR canonical within edit-distance 0.6 of {expected!r})"
            )
        else:
            suggested = f"update fixture: rename {expected!r} → {closest[0]!r}"
    return Mismatch(
        rule_class=rule_class,
        fixture_entry_text=fixture_text,
        expected_before_canonical=expected,
        closest_ir_canonicals=closest,
        suggested_action=suggested,
    )


# --------------------------------------------------------------------------
# Entry-clone helper (frozen Pydantic — model_copy is the supported path)
# --------------------------------------------------------------------------


def _clone_entry(entry: IndexEntry, **updates: object) -> IndexEntry:
    """Return a new IndexEntry with the given field updates.

    Uses Pydantic v2's ``model_copy`` (frozen-friendly). Never mutates
    the input — Lock #5 by-construction.
    """
    return entry.model_copy(update=updates)


# --------------------------------------------------------------------------
# Per-R-class apply functions
#
# Each function returns a 4-tuple
#   (new_entries, mismatches, xref_additions, stem_additions)
# so the orchestrator can accumulate side-channels uniformly.
# --------------------------------------------------------------------------


def _apply_one_R4(
    rule: R4DeleteEntryRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R4: drop the matched canonical from the rendered list."""
    matched = [e for e in entries if e.canonical == rule.term]
    if matched:
        new_entries = [e for e in entries if e.canonical != rule.term]
        return new_entries, [], set(), set()
    # Target absent. On re-apply (allow_stale=True) this is the expected
    # post-state — silent no-op (D-02 / D-04 unification). On first apply,
    # this is a mismatch.
    canonicals = [e.canonical for e in entries]
    mm = _mismatch(
        "R4_delete_entry",
        f"R4DeleteEntryRule(term={rule.term!r})",
        rule.term,
        canonicals,
    )
    return entries, [mm], set(), set()


def _apply_one_R5(
    rule: R5DeleteXrefRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R5: IR no-op; emit ``rule.head`` to the xref-removal side-channel.

    Mismatch detection is deferred to the renderer (the cross-ref derivation
    happens after this pass) — we cannot tell here whether a head exists
    in the cross-ref set. The renderer is the source of truth for that.
    """
    return entries, [], {rule.head}, set()


def _apply_one_R6(
    rule: R6PromoteSingleChildRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R6: IR no-op; emit ``rule.parent_stem`` to the synth-suppress channel.

    Mismatch detection is deferred to the renderer — synthesized stems are
    derived at render time from the IR; we cannot tell here whether the
    stem exists. The renderer is the source of truth.
    """
    return entries, [], set(), {rule.parent_stem}


def _apply_one_R7(
    rule: R7FoldDoubledWordRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R7: merge ``artifact``'s locators into ``canonical``; drop ``artifact``.

    Sort merged locators by ``(section_ref, folio)`` for byte-determinism;
    dedupe by the same key.
    """
    artifact = next(
        (e for e in entries if e.canonical == rule.artifact), None
    )
    target = next(
        (e for e in entries if e.canonical == rule.canonical), None
    )

    if artifact is not None and target is not None:
        # Merge locators. Locator instances are Pydantic; comparable via
        # (section_ref, folio) tuple as the dedup key (mirrors
        # parent_dedup._locator_set).
        seen: dict[tuple[str, str], Locator] = {}
        for loc in list(target.locators) + list(artifact.locators):
            key = (loc.section_ref, loc.folio)
            seen.setdefault(key, loc)
        merged = sorted(seen.values(), key=lambda lo: (lo.section_ref, lo.folio))
        new_target = _clone_entry(target, locators=list(merged))
        new_entries = [
            new_target if e.canonical == rule.canonical
            else e
            for e in entries
            if e.canonical != rule.artifact
        ]
        return new_entries, [], set(), set()

    # _already_applied_R7: artifact absent AND target present → silent no-op.
    if artifact is None and target is not None:
        return entries, [], set(), set()

    # Target absent or both absent → mismatch.
    canonicals = [e.canonical for e in entries]
    mm = _mismatch(
        "R7_fold_doubled_word",
        f"R7FoldDoubledWordRule(artifact={rule.artifact!r}, canonical={rule.canonical!r})",
        rule.canonical if target is None else rule.artifact,
        canonicals,
    )
    return entries, [mm], set(), set()


def _apply_one_R3(
    rule: R3RewordRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R3: rename canonical from ``before`` to ``after``.

    Ambiguity (D-03): 2+ matches → mismatch with ``ambiguous`` suggestion.
    _already_applied: ``after`` already in canonicals AND ``before`` absent.
    """
    matches = [e for e in entries if e.canonical == rule.before]
    if len(matches) >= 2:
        mm = _mismatch(
            "R3_reword",
            f"R3RewordRule(before={rule.before!r}, after={rule.after!r})",
            rule.before,
            [e.canonical for e in entries],
            suggested=(
                f"ambiguous: {len(matches)} entries with canonical="
                f"{rule.before!r}; fix Phase 4 dedup upstream"
            ),
        )
        return entries, [mm], set(), set()
    if len(matches) == 1:
        renamed = _clone_entry(matches[0], canonical=rule.after)
        new_entries = [
            renamed if e.canonical == rule.before else e for e in entries
        ]
        return new_entries, [], set(), set()
    # No matches. _already_applied: after-state present → silent no-op.
    if any(e.canonical == rule.after for e in entries):
        return entries, [], set(), set()
    # Target absent — mismatch.
    canonicals = [e.canonical for e in entries]
    mm = _mismatch(
        "R3_reword",
        f"R3RewordRule(before={rule.before!r}, after={rule.after!r})",
        rule.before,
        canonicals,
    )
    return entries, [mm], set(), set()


def _apply_one_R8(
    rule: R8PluralCanonicalRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R8: rename canonical from ``singular`` to ``plural``.

    _already_applied: plural already in canonicals AND singular absent.
    """
    matches = [e for e in entries if e.canonical == rule.singular]
    if matches:
        new_entries = [
            _clone_entry(e, canonical=rule.plural)
            if e.canonical == rule.singular
            else e
            for e in entries
        ]
        return new_entries, [], set(), set()
    # _already_applied: plural already present → silent no-op.
    if any(e.canonical == rule.plural for e in entries):
        return entries, [], set(), set()
    # Mismatch.
    canonicals = [e.canonical for e in entries]
    mm = _mismatch(
        "R8_plural_canonical",
        f"R8PluralCanonicalRule(singular={rule.singular!r}, plural={rule.plural!r})",
        rule.singular,
        canonicals,
    )
    return entries, [mm], set(), set()


def _apply_one_R2(
    rule: R2RecapitalizeRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R2: acronym caps fix on canonical.

    Match by ``e.canonical.lower() == rule.wrong.lower()``; replace with
    ``rule.right`` (which differs from ``e.canonical`` only in case — the
    Pydantic record's ``_strict_guard`` already enforces letters-only-equal).

    _already_applied: ``rule.right`` already exact-equal in canonicals.
    """
    if any(e.canonical == rule.right for e in entries):
        return entries, [], set(), set()
    matches = [e for e in entries if e.canonical.lower() == rule.wrong.lower()]
    if matches:
        new_entries = [
            _clone_entry(e, canonical=rule.right)
            if e.canonical.lower() == rule.wrong.lower()
            else e
            for e in entries
        ]
        return new_entries, [], set(), set()
    canonicals = [e.canonical for e in entries]
    mm = _mismatch(
        "R2_recapitalize",
        f"R2RecapitalizeRule(wrong={rule.wrong!r}, right={rule.right!r})",
        rule.wrong,
        canonicals,
    )
    return entries, [mm], set(), set()


def _apply_one_R1(
    rule: R1StripVariantsRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R1: scrub the matched canonical's ``variants`` list to []."""
    matches = [e for e in entries if e.canonical == rule.term]
    if not matches:
        canonicals = [e.canonical for e in entries]
        mm = _mismatch(
            "R1_strip_variants",
            f"R1StripVariantsRule(term={rule.term!r})",
            rule.term,
            canonicals,
        )
        return entries, [mm], set(), set()
    # _already_applied: target found AND variants already empty → silent no-op.
    target = matches[0]
    if not target.variants:
        return entries, [], set(), set()
    new_entries = [
        _clone_entry(e, variants=[]) if e.canonical == rule.term else e
        for e in entries
    ]
    return new_entries, [], set(), set()


def _apply_one_R9(
    rule: R9WhitespaceRule,
    entries: list[IndexEntry],
    *,
    allow_stale: bool,
) -> tuple[list[IndexEntry], list[Mismatch], set[str], set[str]]:
    """R9: IR no-op. The text-half is handled by ``apply_r9_whitespace``
    at the renderer's post-emit boundary.
    """
    return entries, [], set(), set()


# --------------------------------------------------------------------------
# Type-keyed dispatch table — keyed by D-01 R-section name.
# --------------------------------------------------------------------------


_DISPATCH = {
    "R4_delete_entry": _apply_one_R4,
    "R5_delete_xref": _apply_one_R5,
    "R6_promote_single_child": _apply_one_R6,
    "R7_fold_doubled_word": _apply_one_R7,
    "R3_reword": _apply_one_R3,
    "R8_plural_canonical": _apply_one_R8,
    "R2_recapitalize": _apply_one_R2,
    "R1_strip_variants": _apply_one_R1,
    "R9_whitespace": _apply_one_R9,
}


# --------------------------------------------------------------------------
# Public API — single pure function over (entries, fixture)
# --------------------------------------------------------------------------


def apply_editorial_overrides(
    entries: list[IndexEntry],
    fixture: EditorialOverrides,
    *,
    allow_stale: bool = False,
) -> ApplyPassResult:
    """Apply the 9 R-class fixture rules to ``entries`` in D-01 order.

    Args:
        entries: pre-emit IR projection — typically Phase 4 ``tree.entries``
            after the cruft+removal filter (the renderer's ``surviving_entries``).
        fixture: validated curator-signed ``EditorialOverrides`` instance.
        allow_stale: when True (set by renderers reading
            ``ALLOW_STALE_OVERRIDES=1`` env-flag), missing-target rules are
            silent no-ops; mismatches are still recorded for the audit
            report but no exception is raised. When False (default — the
            CI / canonical pipeline path), any mismatch raises
            ``EditorialOverrideMismatch``.

    Returns:
        ApplyPassResult with the projected entries, the mismatch list, and
        the R5 / R6 side-channel sets the renderer must consume.

    Raises:
        EditorialOverrideMismatch: when ``allow_stale=False`` AND one or
            more fixture entries failed to match their IR target.

    Determinism: per-section iteration uses YAML document order (the
    Pydantic list preserves it). Inter-class dispatch is the locked
    ``_APPLY_ORDER`` tuple. Locator-merge in R7 is sorted by
    ``(section_ref, folio)``. No randomness, no global state, no I/O —
    Lock #5 by-construction.
    """
    current = list(entries)
    all_mismatches: list[Mismatch] = []
    xref_removal: set[str] = set()
    synth_suppress: set[str] = set()

    for section_name in _APPLY_ORDER:
        rules = getattr(fixture, section_name, [])
        if not rules:
            continue
        applier = _DISPATCH[section_name]
        for rule in rules:
            current, mms, xrefs, stems = applier(
                rule, current, allow_stale=allow_stale
            )
            all_mismatches.extend(mms)
            xref_removal |= xrefs
            synth_suppress |= stems

    if all_mismatches and not allow_stale:
        raise EditorialOverrideMismatch(all_mismatches)

    return ApplyPassResult(
        entries=current,
        mismatches=tuple(all_mismatches),
        xref_removal_set=frozenset(xref_removal),
        synth_suppressed_stems=frozenset(synth_suppress),
    )


# --------------------------------------------------------------------------
# R9 text-half — post-emit string replace (mirrors apply_recap_pairs shape)
# --------------------------------------------------------------------------


def apply_r9_whitespace(
    text: str,
    rules: list[R9WhitespaceRule] | tuple[R9WhitespaceRule, ...],
) -> str:
    """Apply R9 whitespace rules to rendered text via simple string replace.

    YAML document order is preserved (rules iterated in fixture order).
    Each rule is an exact-string substitution — no regex, no normalization
    beyond what the curator authored.
    """
    for rule in rules:
        text = text.replace(rule.before, rule.after)
    return text


# --------------------------------------------------------------------------
# Audit report (D-08) — always emitted; clean header on clean run
# --------------------------------------------------------------------------


def write_mismatch_report(
    mismatches: tuple[Mismatch, ...] | list[Mismatch],
    path: Path,
) -> None:
    """Emit a byte-deterministic markdown audit report (D-08).

    The file is ALWAYS written — clean header + "No mismatches" on a clean
    run, full per-mismatch sections sorted by (rule_class,
    expected_before_canonical) on a stale-fixture run. Included in QUAL-01
    two-runs-diff.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = [
        "# Editorial Override Mismatches",
        "",
        "Source: `src/book_indexer/render/editorial_overrides.py::apply_editorial_overrides`",
        "",
    ]
    if not mismatches:
        parts.append("No mismatches.")
        parts.append("")
        path.write_text("\n".join(parts), encoding="utf-8")
        return
    sorted_mms = sorted(
        mismatches,
        key=lambda m: (m.rule_class, m.expected_before_canonical),
    )
    for mm in sorted_mms:
        parts.append(mm.format())
    path.write_text("\n".join(parts), encoding="utf-8")


# --------------------------------------------------------------------------
# stderr logger (mirrors parent_dedup.py:398-405 style)
# --------------------------------------------------------------------------


def _log(msg: str) -> None:  # pragma: no cover — diagnostic only
    print(f"[editorial_overrides] {msg}", file=sys.stderr)
