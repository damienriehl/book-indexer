"""OUT-01 — Markdown index renderer (Phase 5 Wave 2).

Emits the user-facing `index.md` deliverable per CONTEXT D-07 (body-only —
NO front matter / title page / TOC) and RESEARCH §H-7 (top-level shape).

Top-level structure:
    <!--
    book-indexer metadata
    key=value          (11 lines, alphabetical)
    -->

    # Subject Index

    ## A
    admissibility, § 2.04 (p. 19)

    ## E
    evidence (synthesized)
        admissible evidence, § 2.04 (p. 19)
        ...

    # Table of Cases
    *Jones v. Barnes*, 463 U.S. 745 (1983), § 1.04 (p. 58)

    # Table of Statutes
    42 U.S.C. § 1983, § 2.05 (p. 20)

    # Table of Rules
    FRE 404, § 2.06 (p. 90)
        FRE 404(b)(1), § 2.06 (p. 91)

Critical byte-level guarantees:
  - LF line endings only (Pitfall §P-3 — NEVER CRLF). Bytes-mode write.
  - Literal U+00A0 (`\\xc2\\xa0`) between § and section number AND between
    p./pp. and folio digits. NEVER `&nbsp;` (Pitfall §P-4).
  - U+2013 EN DASH (`\\xe2\\x80\\x93`) for ranges. NEVER U+002D hyphen.
  - Sorted iterations everywhere: by `sort_key` for entries, by stem for
    synthetics, by (-len, alphabetical) for variants. Lock #5 byte-identity
    by-construction.

D-02 — italic case-derived concepts in subject index DEFERRED to v1.x.
`IndexEntry.derived_from_table` is IGNORED here (renderers emit regular
text). The Table of Cases italicizes case names independently (TAB-01).

requirements_addressed: OUT-01.
"""
from __future__ import annotations

import os
import re
from collections import OrderedDict
from pathlib import Path

from book_indexer.curator import (
    CuratorOverrides,
    apply_recap_pairs,
    assert_letters_only,
    is_droppable_plural_variant,
)
from book_indexer.curator.fixture import EditorialOverrides

from .cross_refs import CrossRefEntry, derive_cross_refs
from .editorial_overrides import (
    apply_editorial_overrides,
    apply_r9_whitespace,
    write_mismatch_report,
)
from .filter import auto_strip_xref, is_cruft, is_removed
from .ir import IndexEntry, IndexTree, SubEntry, SyntheticEntry
from .metadata import Metadata
from .parent_dedup import dedupe_parent_aliased_standalones
from .plural_consolidation import (
    ConsolidatedEntry,
    consolidate_plural_pairs,
)
from .range_collapse import collapse_locators
from book_indexer.tables.ir import (
    CaseEntry,
    Locator,
    RuleEntry,
    StatuteEntry,
    SubsectionEntry,
    TableOfCases,
    TableOfRules,
    TableOfStatutes,
)

__all__ = ["render_markdown"]


# --------------------------------------------------------------------------
# B-11 — Statute newline-duplicate collapse (render-path only)
# --------------------------------------------------------------------------
#
# Per CONTEXT 06 D-02 + RESEARCH §H-4: eyecite (Phase 3b) preserves
# line-break artifacts in canonical_citation — e.g., '28 U.S.C. Sec. \n1407'
# AND '28 U.S.C. Sec. 1407' both surface as DISTINCT rows in
# artifacts/tables/statutes.json (audit faithfulness). The fix collapses
# them ONLY at render time. The Phase 3b IR + audit ledger are UNTOUCHED.


def _normalize_statute_canonical(raw: str) -> str:
    """B-11: collapse internal whitespace to single space; strip ends."""
    return re.sub(r"\s+", " ", raw).strip()


def _dedup_statute_entries(entries: list[StatuteEntry]) -> list[StatuteEntry]:
    """B-11: merge entries whose canonical_citation collapses to the same key.

    Returns OrderedDict-preserved list ordered by first-occurrence. Locator
    lists are unioned (deduped by (section_ref, folio) tuple) per RESEARCH
    §H-4. Each merged StatuteEntry has its canonical_citation + display_name
    rewritten to the normalized form.
    """
    seen: "OrderedDict[str, StatuteEntry]" = OrderedDict()
    for entry in entries:
        key = _normalize_statute_canonical(entry.canonical_citation)
        if key in seen:
            existing = seen[key]
            merged_locs: "OrderedDict[tuple[str, str], Locator]" = OrderedDict()
            for loc in existing.locators:
                merged_locs[(loc.section_ref, loc.folio)] = loc
            for loc in entry.locators:
                merged_locs.setdefault((loc.section_ref, loc.folio), loc)
            seen[key] = existing.model_copy(
                update={"locators": list(merged_locs.values())}
            )
        else:
            seen[key] = entry.model_copy(
                update={
                    "canonical_citation": key,
                    "display_name": key,
                }
            )
    return list(seen.values())


# Locator-list separator inside an entry line (CONTEXT D-07 / RESEARCH §H-7).
_LOCATOR_SEP = ", "
# Variant separator inside the *(also: ...)* parenthetical. Semicolon avoids
# conflict with locator-list commas (RESEARCH §H-7 + Open Q1).
_VARIANT_SEP = "; "
# Sub-entry indent (treatise convention; RESEARCH §H-7).
_SUBENTRY_INDENT = "    "
# Maximum number of variants emitted in any (also: ...) parenthetical.
_MAX_VARIANTS = 3


# --------------------------------------------------------------------------
# Variant filter (CONTEXT D-04 / B-08)
# --------------------------------------------------------------------------


def _filter_variants(
    canonical: str,
    variants: list[str],
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Drop case-only + cruft-shaped + (CUR-03) plural variants; sort + top-3.

    Rules (in order):
      - Drop if ``variant.lower() == canonical.lower()`` (case-only).
      - Drop if ``is_cruft(variant)`` (B-05 cruft filter — Pitfall §P-4
        single source of truth).
      - **CUR-03 (D-13):** drop if
        ``is_droppable_plural_variant(canonical, variant, keep_plural_set)``.
        ``keep_plural_set`` is the curator-confirmed exclusion list of
        canonicals whose plurals must be preserved. Default ``frozenset()``
        keeps existing tests backward-compatible (3-rule behavior).
      - Dedup by lowercase form.
      - Sort surviving variants by ``(-len(v), v)`` — length-desc, alpha.
      - Take top ``_MAX_VARIANTS``.

    The IR is untouched — ``IndexEntry.variants`` is read but never mutated.
    Phase 4 lemma bucketing already merged every surface form's locators
    into ``IndexEntry.locators`` so dropping a plural surface form here
    does NOT lose any locators (Test 20 in Plan 07-03 ``<behavior>``).

    Args:
        canonical: the entry's canonical form.
        variants: the IR-side variants list (read-only).
        keep_plural_set: ``frozenset`` of canonicals (case-folded) whose
            plural variants are curator-protected. Sourced from
            ``CuratorOverrides.keep_plural_set``.
        dropped_plural_acc: optional accumulator — when provided, every
            ``(canonical, variant)`` pair dropped by the CUR-03 plural rule
            is appended for coverage.md "Curator Pass" logging.

    Returns:
        list[str] — survivor variants (top-3, length-desc/alpha-sorted).
    """
    canon_lower = canonical.lower()
    survivors: list[str] = []
    seen: set[str] = set()
    for v in variants:
        if v.lower() == canon_lower:
            continue
        if is_cruft(v):
            continue
        # CUR-03: drop pure-plural variants unless the canonical (or its
        # plural form) is curator-protected via keep_plural_set.
        if is_droppable_plural_variant(canonical, v, keep_plural_set):
            if dropped_plural_acc is not None:
                dropped_plural_acc.append((canonical, v))
            continue
        # Dedup by lowercase form.
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        survivors.append(v)

    survivors.sort(key=lambda v: (-len(v), v))
    return survivors[:_MAX_VARIANTS]


def _render_variants(
    canonical: str,
    variants: list[str],
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
) -> str:
    """Return ' *(also: v1; v2; v3)*' or '' if no surviving variants."""
    filtered = _filter_variants(canonical, variants, keep_plural_set, dropped_plural_acc)
    if not filtered:
        return ""
    return " *(also: " + _VARIANT_SEP.join(filtered) + ")*"


# --------------------------------------------------------------------------
# Locator rendering
# --------------------------------------------------------------------------


def _render_locators(locators: list[Locator]) -> str:
    """Apply D-03 collapse + join with ', '. Always sorted (collapse_locators
    sorts internally)."""
    formatted = collapse_locators(locators)
    return _LOCATOR_SEP.join(fl.rendered for fl in formatted)


# --------------------------------------------------------------------------
# Metadata block (AUD-04 — RESEARCH §H-11)
# --------------------------------------------------------------------------


def _render_metadata_block(metadata: Metadata) -> str:
    """Emit a 12-line HTML comment block.

    Layout:
        <!--
        book-indexer metadata
        key1=value1
        ...
        key11=value11
        -->

    Inside-comment line count: 1 header + 11 key=value pairs = 12 lines.
    Keys are emitted in ASCII-alphabetical order (Lock #5 determinism).
    The `built_at` Lock #5 sentinel is excluded from the visible block —
    it's the frozen Unix-epoch sentinel and would be redundant noise.
    """
    payload = metadata.model_dump()
    payload.pop("built_at", None)
    lines: list[str] = ["<!--", "book-indexer metadata"]
    for key in sorted(payload):
        value = payload[key]
        lines.append(f"{key}={value}")
    lines.append("-->")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Subject index — entries + synthetics (B-06) + letter dividers
# --------------------------------------------------------------------------


def _entry_letter(sort_key: str) -> str:
    """First letter of sort_key (uppercased) for letter-divider bucketing."""
    return sort_key[0].upper() if sort_key else ""


def _render_subentry_line(sub: SubEntry) -> str:
    return f"{_SUBENTRY_INDENT}{sub.text}, {_render_locators(sub.locators)}"


def _render_entry_lines(
    entry: IndexEntry,
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
    removal_set: frozenset[str] = frozenset(),
) -> list[str]:
    """One main line per entry plus indented sub-entries.

    Threads ``keep_plural_set`` + ``dropped_plural_acc`` through to the
    variant filter (CUR-03 plural-variant drop). The optional
    ``removal_set`` arg propagates CUR-01 to sub-entry filtering — sub-entries
    whose ``text`` is in ``removal_set`` are dropped (RESEARCH §Pitfall 4
    sub-entry inheritance: a sub-entry whose surface form matches a
    curator-removed canonical must also be dropped). Default kwargs keep
    the existing 3-rule behavior backward-compatible for callers without
    overrides.
    """
    lines: list[str] = []
    variants_str = _render_variants(
        entry.canonical, entry.variants, keep_plural_set, dropped_plural_acc
    )
    head = f"{entry.canonical}{variants_str}"
    locator_str = _render_locators(entry.locators)
    if locator_str:
        lines.append(f"{head}, {locator_str}")
    else:
        lines.append(head)
    # Sub-entries sorted by their own sort_key for determinism.
    # CUR-01 sub-entry filtering: drop subs whose surface text matches a
    # curator-removed canonical. Default empty removal_set preserves v1.0
    # behavior for callers without curator overrides.
    for sub in sorted(entry.sub_entries, key=lambda s: s.sort_key):
        if removal_set and is_removed(sub.text, removal_set):
            continue
        lines.append(_render_subentry_line(sub))
    return lines


def _render_synthetic_lines(
    synth: SyntheticEntry,
    entries_by_canonical: dict[str, IndexEntry],
    removal_set: frozenset[str] = frozenset(),
    transferred_variants: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Multi-line block:
        stem
            sib1, locator-list
            sib2, locator-list
    Sibling sort order: alphabetical by canonical.

    CUR-03 sub-rule b (CONTEXT D-13): the literal ``(synthesized)`` marker
    is NOT emitted — it has no semantic value for a reader. The IR field
    ``IndexEntry.synthesized`` (and ``SyntheticEntry`` itself) is unchanged;
    this is render-time projection only.

    CUR-01 sub-rule c (RESEARCH §Pitfall 4): sibling canonicals that match
    a curator-removed term are dropped from the rendered synthetic block —
    they would otherwise leak through `_render_subject_index`'s top-level
    is_removed filter (the sibling is rendered AS A SUB-ENTRY of the
    synthetic stem, not as a top-level entry).

    v1.2.2: ``transferred_variants`` carries the side-channel
    ``{child_canonical: variants}`` map produced by
    ``dedupe_parent_aliased_standalones`` — when a same-first-word
    standalone is dropped, its ``*(also: …)*`` parenthetical is
    re-attached here on the surviving child line. The IR is never
    mutated; this is render-time projection only (mirrors the
    ``(synthesized)`` marker pattern).
    """
    transferred_variants = transferred_variants or {}
    lines = [synth.stem]
    for sib_canonical in sorted(synth.sibling_canonicals):
        if removal_set and is_removed(sib_canonical, removal_set):
            continue
        # Look up the sibling entry's locators if we have it; otherwise
        # fall back to the synthetic's union (defensive — synthesizer
        # already deduped). For the reference corpus we have it.
        sib_entry = entries_by_canonical.get(sib_canonical)
        if sib_entry is not None:
            loc_str = _render_locators(sib_entry.locators)
        else:
            # Fallback: emit the synthetic's union locators (rare path).
            loc_str = _render_locators(list(synth.locators))
        # v1.2.2: optional inline (also: ...) parenthetical transferred
        # from a now-removed standalone. _render_variants applies the
        # SAME filter chain (CUR-03 plural-variant drop) used on
        # standalone entries, so byte-identity is preserved.
        variants = transferred_variants.get(sib_canonical, ())
        variants_str = (
            _render_variants(sib_canonical, list(variants)) if variants else ""
        )
        lines.append(
            f"{_SUBENTRY_INDENT}{sib_canonical}{variants_str}, {loc_str}"
        )
    return lines


def _render_cross_ref_line(xref: CrossRefEntry) -> str:
    """Format a head-noun cross-ref as ``head. See primary canonical.``

    Used by ``_render_subject_index`` when the third merge-kind
    (``"xref"``) is encountered. The trailing period AFTER ``See <target>``
    follows the legal-treatise convention (cf. Bluebook ``See`` cross-refs,
    legal-treatise chapter conventions). No locator is emitted — a
    cross-ref is a pointer, not a citation.
    """
    return f"{xref.head}. See {xref.primary_canonical}."


def _render_consolidated_line(consolidated: ConsolidatedEntry) -> str:
    """v1.2.1: format a ConsolidatedEntry as one of:

      ``term(s). See target.``               (xref source_kind)
      ``term(s), § N.NN (p. N), …``          (primary source_kind)

    The display canonical (``term(s)`` / ``term(es)`` / ``term(ies)``)
    is the user-locked format from the v1.2.1 plan. For primary
    consolidations, locators are taken verbatim from the surviving
    primary IndexEntry (already-verified — Lock #1 preserved).
    """
    if consolidated.source_kind == "xref":
        return f"{consolidated.display_canonical}. See {consolidated.see_target}."
    # primary
    locator_str = _render_locators(list(consolidated.locators))  # type: ignore[arg-type]
    if locator_str:
        return f"{consolidated.display_canonical}, {locator_str}"
    return consolidated.display_canonical


def _render_subject_index(
    tree: IndexTree,
    synthetics: list[SyntheticEntry],
    removal_set: frozenset[str] = frozenset(),
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
    editorial_overrides: EditorialOverrides | None = None,
    allow_stale_overrides: bool = False,
) -> str:
    """Build the # Subject Index section with letter dividers and synthetics
    integrated alphabetically.

    Phase 7 Wave 3 extensions:
      - **CUR-01 (D-04):** drops every ``IndexEntry`` whose canonical is in
        ``removal_set``. Sub-entry inheritance is automatic — the parent's
        entry-line short-circuit drops the parent and ALL its sub-entries
        in one go (RESEARCH §Pitfall 4).
      - **CUR-03 (D-13):** ``keep_plural_set`` + ``dropped_plural_acc``
        threaded through to ``_render_entry_lines`` → ``_render_variants``
        → ``_filter_variants``. Plural variants are dropped from
        ``(also: …)`` parentheticals at render time only; IR untouched.
      - Default ``frozenset()`` arguments keep all pre-Phase-7 callers
        backward-compatible.

    Dangling cross-reference cleanup runs as a *post-pass* in
    ``render_markdown`` via ``filter.auto_strip_xref(text, removal_set)``
    AFTER all entries are rendered. That keeps this loop simple and
    operates on the same string-shape regardless of which renderer
    produced the text.
    """
    # Filter cruft + curator-removed entries at render time
    # (D-04 / B-05 + CUR-01).
    surviving_entries = [
        e
        for e in tree.entries
        if not is_cruft(e.canonical) and not is_removed(e.canonical, removal_set)
    ]

    # Phase 9 — 7th projection pass. Operates on the IR-side entries list
    # BEFORE the merged-stream construction (CONTEXT D-01 / D-02). The
    # apply-pass call site is the architecturally-correct insertion point:
    # parent_dedup runs LATER on the merged stream, but apply_editorial_overrides
    # is a pure function over `list[IndexEntry]` (Wave 2 plan task 2.2 —
    # variable-name substitution noted: the plan's `entries` corresponds to
    # this `surviving_entries` list). R5 / R6 side-channels filter cross-refs
    # and synthetics below; R9 is post-emit text-only (handled in the public
    # renderer at the apply_recap_pairs hook).
    _xref_removal_set: frozenset[str] = frozenset()
    _synth_suppressed_stems: frozenset[str] = frozenset()
    if editorial_overrides is not None:
        _apply_result = apply_editorial_overrides(
            surviving_entries,
            editorial_overrides,
            allow_stale=allow_stale_overrides,
        )
        surviving_entries = _apply_result.entries
        _xref_removal_set = _apply_result.xref_removal_set
        _synth_suppressed_stems = _apply_result.synth_suppressed_stems
        # D-08: ALWAYS emit the audit report (clean header on clean run).
        write_mismatch_report(
            _apply_result.mismatches,
            Path("artifacts/audit/editorial_override_mismatches.md"),
        )

    # Index entries by canonical for synthetic lookup.
    entries_by_canonical = {e.canonical: e for e in surviving_entries}

    # UAT 08-1 head-noun cross-refs (auto-detect, no curator-gate).
    # Derived from the SURVIVING entries + synthetics so cross-refs never
    # point at removed canonicals and never duplicate an existing
    # alphabetical anchor (top-level canonical or B-06 synthesized stem).
    # Phase 9 — R6 suppresses synthesized parents whose stem is in the
    # editorial-overrides side-channel.
    surviving_synthetics = [
        s for s in synthetics
        if not is_removed(s.stem, removal_set)
        and s.stem not in _synth_suppressed_stems
    ]
    cross_refs = derive_cross_refs(surviving_entries, surviving_synthetics)
    # Phase 9 — R5 filters cross-refs whose head is in the side-channel.
    if _xref_removal_set:
        cross_refs = [x for x in cross_refs if x.head not in _xref_removal_set]

    # Sort survivors and synthetics independently, then merge by sort key.
    sorted_entries = sorted(surviving_entries, key=lambda e: e.sort_key)
    sorted_synthetics = sorted(surviving_synthetics, key=lambda s: s.stem)

    # Merge: each item carries (sort_key, kind, payload).
    merged: list[tuple[str, str, object]] = []
    for e in sorted_entries:
        merged.append((e.sort_key, "entry", e))
    for s in sorted_synthetics:
        # Synthetic stem is the bucket key (sort by stem, lowercased).
        merged.append((s.stem.lower(), "synth", s))
    for x in cross_refs:
        # UAT 08-1: alphabetical head-noun cross-refs land at their
        # natural position. sort_key is the head (already lowercased).
        merged.append((x.sort_key, "xref", x))
    merged.sort(key=lambda t: t[0])

    # v1.2.1: collapse adjacent singular/plural pairs into a single
    # ``term(s)`` / ``term(es)`` / ``term(ies)`` line. Pure-functional
    # pre-pass — preserves Lock #5 byte-determinism by-construction.
    merged = consolidate_plural_pairs(merged, keep_plural_set)

    # v1.2.2: drop standalone top-level entries that duplicate B-06
    # synthesized children when the child's first word matches the
    # parent stem (e.g. ``complex case`` standalone removed because it
    # appears as a child under the ``complex`` synth parent).
    # Different-first-word standalones (``manual for complex
    # litigation``) are preserved as alphabetical anchors. Variants
    # from removed standalones are re-emitted via ``transferred_variants``
    # on the surviving child line in ``_render_synthetic_lines``.
    merged, transferred_variants, _dedup_decisions = (
        dedupe_parent_aliased_standalones(merged, entries_by_canonical)
    )

    out: list[str] = ["# Subject Index"]
    current_letter = ""
    for sort_key, kind, payload in merged:
        letter = _entry_letter(sort_key)
        if letter != current_letter:
            current_letter = letter
            out.append("")
            out.append(f"## {letter}")
            out.append("")
        if kind == "entry":
            entry_lines = _render_entry_lines(
                payload,  # type: ignore[arg-type]
                keep_plural_set,
                dropped_plural_acc,
                removal_set,
            )
            out.extend(entry_lines)
        elif kind == "synth":
            syn_lines = _render_synthetic_lines(
                payload,  # type: ignore[arg-type]
                entries_by_canonical,
                removal_set,
                transferred_variants,
            )
            out.extend(syn_lines)
        elif kind == "consolidated":
            out.append(_render_consolidated_line(payload))  # type: ignore[arg-type]
        else:  # xref — UAT 08-1
            out.append(_render_cross_ref_line(payload))  # type: ignore[arg-type]

    return "\n".join(out)


# --------------------------------------------------------------------------
# Tables (Phase 3b consumption)
# --------------------------------------------------------------------------


def _render_table_of_cases(tables: TableOfCases | None) -> str:
    out: list[str] = ["# Table of Cases"]
    if tables is None:
        return "\n".join(out)
    for case in sorted(tables.entries, key=lambda c: c.sort_key):
        out.append("")
        # Italic case display name; followed by reporter/citation; then
        # locators per D-07 / RESEARCH §H-7.
        loc_str = _render_locators(case.locators)
        head = f"*{case.display_name}*, {case.canonical_citation}"
        if loc_str:
            out.append(f"{head}, {loc_str}")
        else:
            out.append(head)
    return "\n".join(out)


def _render_table_of_statutes(tables: TableOfStatutes | None) -> str:
    out: list[str] = ["# Table of Statutes"]
    if tables is None:
        return "\n".join(out)
    # B-11: collapse newline-duplicate canonicals at render time (CONTEXT
    # D-02 — render-path only; Phase 3b IR untouched).
    deduped = _dedup_statute_entries(tables.entries)
    for s in sorted(deduped, key=lambda x: x.sort_key):
        out.append("")
        loc_str = _render_locators(s.locators)
        if loc_str:
            out.append(f"{s.display_name}, {loc_str}")
        else:
            out.append(s.display_name)
    return "\n".join(out)


def _render_table_of_rules(tables: TableOfRules | None) -> str:
    out: list[str] = ["# Table of Rules"]
    if tables is None:
        return "\n".join(out)
    for rule in sorted(tables.entries, key=lambda r: r.sort_key):
        out.append("")
        # Parent line.
        parent_loc = _render_locators(rule.parent_locators)
        if parent_loc:
            out.append(f"{rule.parent_rule}, {parent_loc}")
        else:
            out.append(rule.parent_rule)
        # Subsection lines (4-space indent), sorted lex by subsection_path.
        for sub in sorted(rule.subsections, key=lambda s: s.subsection_path):
            sub_loc = _render_locators(sub.locators)
            label = f"{rule.parent_rule}{sub.subsection_path}"
            if sub_loc:
                out.append(f"{_SUBENTRY_INDENT}{label}, {sub_loc}")
            else:
                out.append(f"{_SUBENTRY_INDENT}{label}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def render_markdown(
    tree: IndexTree,
    synthetics: list[SyntheticEntry],
    tables: dict[str, object],
    metadata: Metadata,
    overrides: CuratorOverrides | None = None,
    editorial_overrides: EditorialOverrides | None = None,
    curator_log: dict[str, list] | None = None,
) -> bytes:
    """Render OUT-01 — index.md as UTF-8 bytes (LF line endings, no CRLF).

    Phase 7 Wave 3: when ``overrides`` is provided, applies the full
    curator pass:

      * **CUR-01 (D-04):** drops every entry in ``overrides.removal_set``;
        sub-entries inherit the drop. After the body is rendered, runs
        ``auto_strip_xref(text, removal_set)`` to clean dangling
        ``See <removed>`` / ``(also: …)`` references on surviving entries.
      * **CUR-02 (D-05):** applies ``apply_recap_pairs(text, pairs)`` as a
        pre-emit exact-string pass to the FULL rendered body (subject
        index + tables). Strict guard
        (``assert_letters_only`` re-run for defense in depth) hard-fails
        the build on any letter delta beyond case.
      * **CUR-03 (D-13):** drops pure-plural variants from
        ``(also: …)`` parentheticals via
        ``is_droppable_plural_variant`` consulted in
        ``_filter_variants`` (sub-rule a). The ``(synthesized)`` marker
        is unconditionally suppressed (sub-rule b — see
        ``_render_synthetic_lines``).

    Audit-faithfulness invariant: ``index_evidence.json``,
    ``index_tree.json``, ``sections.json``, ``page_corpus.txt`` are NOT
    touched (D-05). Recap is render-output-only; the IR is unchanged.

    Args:
        tree: Phase 4 IndexTree (read-only consumer).
        synthetics: list of B-06 SyntheticEntry projections.
        tables: dict with optional 'cases', 'statutes', 'rules' keys.
        metadata: AUD-04 Metadata Pydantic model.
        overrides: optional curator overrides (CUR-01 + CUR-02 + CUR-03);
            ``None`` reproduces the v1.0 behavior exactly.
        curator_log: optional dict — when provided AND ``overrides`` is
            non-None, the function appends to:
              ``curator_log["dangling_xrefs_stripped"]`` (list[str])
              ``curator_log["dropped_plural_variants"]`` (list[tuple[str,str]])
            Used by ``coverage.py`` to emit the "Curator Pass" audit
            section. The accumulator is filled in-place; pass an empty
            dict and read it after.

    Returns:
        bytes — UTF-8-encoded markdown. Caller does the atomic write
        (Pitfall §P-3 — NEVER `Path.write_text` to avoid platform CRLF).
    """
    cases_table = tables.get("cases")  # type: ignore[union-attr]
    statutes_table = tables.get("statutes")  # type: ignore[union-attr]
    rules_table = tables.get("rules")  # type: ignore[union-attr]

    removal_set = overrides.removal_set if overrides else frozenset()
    keep_plural_set = overrides.keep_plural_set if overrides else frozenset()
    dropped_plural_acc: list[tuple[str, str]] = []
    # Phase 9 — D-07 ALLOW_STALE_OVERRIDES env-flag is read once per render
    # call; never propagated as a module global. CI never sets it.
    allow_stale_overrides = (
        os.environ.get("ALLOW_STALE_OVERRIDES") == "1"
    )

    blocks = [
        _render_metadata_block(metadata),
        _render_subject_index(
            tree, synthetics, removal_set, keep_plural_set, dropped_plural_acc,
            editorial_overrides=editorial_overrides,
            allow_stale_overrides=allow_stale_overrides,
        ),
        _render_table_of_cases(cases_table),  # type: ignore[arg-type]
        _render_table_of_statutes(statutes_table),  # type: ignore[arg-type]
        _render_table_of_rules(rules_table),  # type: ignore[arg-type]
    ]
    text = "\n\n".join(blocks) + "\n"

    stripped_xrefs: list[str] = []
    if overrides is not None:
        # CUR-01 dangling-xref cleanup (D-04). Operates on rendered text.
        text, stripped_xrefs = auto_strip_xref(text, removal_set)
        # CUR-02 recapitalize pre-emit pass (D-05). Strict-guard re-run for
        # defense in depth (the Pydantic record-level validator already
        # rejected letter deltas at fixture-load time).
        if overrides.recapitalize_pairs:
            pairs = [tuple(p) for p in overrides.recapitalize_pairs]
            assert_letters_only(pairs)
            text = apply_recap_pairs(text, pairs)

    # Phase 9 — R9 whitespace post-emit text replace. Mirrors the
    # apply_recap_pairs shape directly above; R9 is the LAST transform
    # (D-01 locked apply order ends with R9).
    if editorial_overrides is not None and editorial_overrides.R9_whitespace:
        text = apply_r9_whitespace(text, editorial_overrides.R9_whitespace)

    if curator_log is not None and overrides is not None:
        curator_log.setdefault("dangling_xrefs_stripped", []).extend(stripped_xrefs)
        curator_log.setdefault("dropped_plural_variants", []).extend(
            dropped_plural_acc
        )

    return text.encode("utf-8")
