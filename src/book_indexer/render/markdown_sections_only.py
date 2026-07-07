"""OUT-05 — Markdown sections-only renderer (Phase 7 Wave 3).

Near-copy of ``markdown.py`` (CONTEXT D-02 — "renderer near-copy boundary")
that emits the *sections-only* output variant. The ONLY structural
differences from ``markdown.py`` are:

  1. Locator formatter swap: ``collapse_locators`` (dual ``§ N.NN (p. N)``)
     → ``collapse_locators_sections_only`` (``§ N.NN`` only; D-03 collapse
     to ``§§ N–M`` ranges).
  2. ``Metadata.pages_only_variant`` is set to ``True`` for this variant
     so AUD-04 / Lock #5 byte-identity tests can pin each variant.

The ``_filter_variants`` / ``_render_subentry_line`` / ``_render_entry_lines``
/ ``_render_synthetic_lines`` / statute helpers are byte-identical to
``markdown.py``. Wave 3 Task 2 then extends BOTH this file and ``markdown.py``
with curator-overrides wiring (CUR-01 removal short-circuit + CUR-02
recapitalize + CUR-03 plural-variant filter + (synthesized)-marker drop).

Pitfall §P-3: bytes-mode write (LF only). Pitfall §P-4: NBSP / EN DASH
constants only — never ASCII space in rendered locators.

requirements_addressed: OUT-05.
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

from .cross_refs import derive_cross_refs
from .editorial_overrides import (
    apply_editorial_overrides,
    apply_r9_whitespace,
    write_mismatch_report,
)
from .filter import auto_strip_xref, is_cruft, is_removed
from .ir import IndexEntry, IndexTree, SubEntry, SyntheticEntry
from .markdown import _render_cross_ref_line
from .metadata import Metadata
from .parent_dedup import dedupe_parent_aliased_standalones
from .plural_consolidation import (
    ConsolidatedEntry,
    consolidate_plural_pairs,
)
from .section_range_collapse import collapse_locators_sections_only
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

__all__ = ["render_markdown_sections_only"]


# --------------------------------------------------------------------------
# B-11 — Statute newline-duplicate collapse (mirrors markdown.py)
# --------------------------------------------------------------------------


def _normalize_statute_canonical(raw: str) -> str:
    """B-11: collapse internal whitespace to single space; strip ends."""
    return re.sub(r"\s+", " ", raw).strip()


def _dedup_statute_entries(entries: list[StatuteEntry]) -> list[StatuteEntry]:
    """B-11 statute newline-duplicate collapse — mirrors markdown.py."""
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


# Locator-list separator — mirrors markdown.py.
_LOCATOR_SEP = ", "
# Variant separator inside the *(also: ...)* parenthetical.
_VARIANT_SEP = "; "
_SUBENTRY_INDENT = "    "
_MAX_VARIANTS = 3


# --------------------------------------------------------------------------
# Variant filter — mirrors markdown.py contract; Task 2 extends with
# is_droppable_plural_variant + keep_plural_set kwarg.
# --------------------------------------------------------------------------


def _filter_variants(
    canonical: str,
    variants: list[str],
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Drop case-only + cruft-shaped + (CUR-03) plural variants; sort + top-3.

    Identical contract to ``markdown.py:_filter_variants`` — the
    sections-only output variant must drop the same plural variants
    (CONTEXT D-13: CUR-03 applies to BOTH variants).
    """
    canon_lower = canonical.lower()
    survivors: list[str] = []
    seen: set[str] = set()
    for v in variants:
        if v.lower() == canon_lower:
            continue
        if is_cruft(v):
            continue
        if is_droppable_plural_variant(canonical, v, keep_plural_set):
            if dropped_plural_acc is not None:
                dropped_plural_acc.append((canonical, v))
            continue
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
# Locator rendering — THE SWAP. sections-only formatter (D-03 collapse;
# emits ``§ N.NN`` / ``§§ N–M``; NEVER ``(p. N)``).
# --------------------------------------------------------------------------


def _render_locators(locators: list[Locator]) -> str:
    """OUT-05: emit only § N.NN (no (p. N)); apply D-03 sections-only collapse."""
    formatted = collapse_locators_sections_only(locators)
    return _LOCATOR_SEP.join(fl.rendered for fl in formatted)


# --------------------------------------------------------------------------
# Metadata block — mirrors markdown.py
# --------------------------------------------------------------------------


def _render_metadata_block(metadata: Metadata) -> str:
    """Emit the alphabetical key=value HTML-comment metadata block.

    The ``pages_only_variant`` field appears in the alphabetical sort and
    distinguishes the two output variants for AUD-04 + Lock #5.
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
# Subject index — entries + synthetics + letter dividers (mirrors markdown.py)
# --------------------------------------------------------------------------


def _entry_letter(sort_key: str) -> str:
    return sort_key[0].upper() if sort_key else ""


def _render_subentry_line(sub: SubEntry) -> str:
    return f"{_SUBENTRY_INDENT}{sub.text}, {_render_locators(sub.locators)}"


def _render_consolidated_line(consolidated: ConsolidatedEntry) -> str:
    """v1.2.1 sections-only: format a ConsolidatedEntry using the
    sections-only locator formatter (no ``(p. N)`` parenthetical).

    Mirrors ``markdown.py:_render_consolidated_line`` shape; only
    delta is the locator formatter swap (D-03 sections-only collapse).
    """
    if consolidated.source_kind == "xref":
        return f"{consolidated.display_canonical}. See {consolidated.see_target}."
    locator_str = _render_locators(list(consolidated.locators))  # type: ignore[arg-type]
    if locator_str:
        return f"{consolidated.display_canonical}, {locator_str}"
    return consolidated.display_canonical


def _render_entry_lines(
    entry: IndexEntry,
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
    removal_set: frozenset[str] = frozenset(),
) -> list[str]:
    """Wave 4 CUR-01 sub-entry inheritance: drop subs whose ``text`` is in
    ``removal_set`` (mirrors ``markdown.py:_render_entry_lines`` Wave 4 fix).
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
    """Synthesized parent + sibling sub-entries.

    CUR-03 sub-rule b (CONTEXT D-13): the literal ``(synthesized)`` marker
    is NOT emitted on the parent line. The IR field
    ``IndexEntry.synthesized`` (and ``SyntheticEntry`` itself) is unchanged;
    this is render-time projection only. Mirrors ``markdown.py``.

    Wave 4 CUR-01 sub-rule c: sibling canonicals matching curator-removed
    terms are dropped from the synthetic block (mirrors markdown.py).

    v1.2.2: ``transferred_variants`` carries the dedup pass's side-channel
    map ``{child_canonical: variants}`` — emit the variants inline on the
    surviving child line. Mirrors ``markdown.py``.
    """
    transferred_variants = transferred_variants or {}
    lines = [synth.stem]
    for sib_canonical in sorted(synth.sibling_canonicals):
        if removal_set and is_removed(sib_canonical, removal_set):
            continue
        sib_entry = entries_by_canonical.get(sib_canonical)
        if sib_entry is not None:
            loc_str = _render_locators(sib_entry.locators)
        else:
            loc_str = _render_locators(list(synth.locators))
        variants = transferred_variants.get(sib_canonical, ())
        variants_str = (
            _render_variants(sib_canonical, list(variants)) if variants else ""
        )
        lines.append(
            f"{_SUBENTRY_INDENT}{sib_canonical}{variants_str}, {loc_str}"
        )
    return lines


def _render_subject_index(
    tree: IndexTree,
    synthetics: list[SyntheticEntry],
    removal_set: frozenset[str] = frozenset(),
    keep_plural_set: frozenset[str] = frozenset(),
    dropped_plural_acc: list[tuple[str, str]] | None = None,
    editorial_overrides: EditorialOverrides | None = None,
    allow_stale_overrides: bool = False,
) -> str:
    surviving_entries = [
        e
        for e in tree.entries
        if not is_cruft(e.canonical) and not is_removed(e.canonical, removal_set)
    ]

    # Phase 9 — 7th projection pass (mirrors markdown.py). Variable-name
    # substitution noted: plan's `entries` corresponds to `surviving_entries`.
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
        write_mismatch_report(
            _apply_result.mismatches,
            Path("artifacts/audit/editorial_override_mismatches.md"),
        )

    entries_by_canonical = {e.canonical: e for e in surviving_entries}

    # UAT 08-1 head-noun cross-refs (mirrors markdown.py). Phase 9 R6
    # filters synth_suppressed_stems; R5 filters cross-refs below.
    surviving_synthetics = [
        s for s in synthetics
        if not is_removed(s.stem, removal_set)
        and s.stem not in _synth_suppressed_stems
    ]
    cross_refs = derive_cross_refs(surviving_entries, surviving_synthetics)
    if _xref_removal_set:
        cross_refs = [x for x in cross_refs if x.head not in _xref_removal_set]

    sorted_entries = sorted(surviving_entries, key=lambda e: e.sort_key)
    sorted_synthetics = sorted(surviving_synthetics, key=lambda s: s.stem)

    merged: list[tuple[str, str, object]] = []
    for e in sorted_entries:
        merged.append((e.sort_key, "entry", e))
    for s in sorted_synthetics:
        merged.append((s.stem.lower(), "synth", s))
    for x in cross_refs:
        merged.append((x.sort_key, "xref", x))
    merged.sort(key=lambda t: t[0])

    # v1.2.1: collapse adjacent singular/plural pairs (mirrors markdown.py).
    merged = consolidate_plural_pairs(merged, keep_plural_set)

    # v1.2.2: parent-aliased-standalone dedup (mirrors markdown.py).
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
# Tables (sections-only locators)
# --------------------------------------------------------------------------


def _render_table_of_cases(tables: TableOfCases | None) -> str:
    out: list[str] = ["# Table of Cases"]
    if tables is None:
        return "\n".join(out)
    for case in sorted(tables.entries, key=lambda c: c.sort_key):
        out.append("")
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
        parent_loc = _render_locators(rule.parent_locators)
        if parent_loc:
            out.append(f"{rule.parent_rule}, {parent_loc}")
        else:
            out.append(rule.parent_rule)
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


def render_markdown_sections_only(
    tree: IndexTree,
    synthetics: list[SyntheticEntry],
    tables: dict[str, object],
    metadata: Metadata,
    overrides: CuratorOverrides | None = None,
    editorial_overrides: EditorialOverrides | None = None,
    curator_log: dict[str, list] | None = None,
) -> bytes:
    """Render OUT-05 — index_sections_only.md as UTF-8 bytes.

    Mirrors ``render_markdown`` extension shape — same curator-overrides
    contract (CUR-01 + CUR-02 + CUR-03) applied to BOTH variants
    (CONTEXT D-04 + D-05 + D-13). Only delta vs. ``render_markdown``:
    locator formatter (sections-only) and ``pages_only_variant=True``.

    Args:
        tree: Phase 4 IndexTree (read-only consumer).
        synthetics: list of B-06 SyntheticEntry projections.
        tables: dict with optional 'cases', 'statutes', 'rules' keys.
        metadata: AUD-04 Metadata Pydantic model. The function clones it
            with ``pages_only_variant=True``.
        overrides: optional curator overrides; ``None`` reproduces v1.0.
        curator_log: optional accumulator dict (mirrors ``render_markdown``).

    Returns:
        bytes — UTF-8-encoded markdown. Caller does the atomic write
        (Pitfall §P-3 — NEVER ``Path.write_text`` to avoid platform CRLF).
    """
    cases_table = tables.get("cases")  # type: ignore[union-attr]
    statutes_table = tables.get("statutes")  # type: ignore[union-attr]
    rules_table = tables.get("rules")  # type: ignore[union-attr]

    metadata_for_block = metadata.model_copy(update={"pages_only_variant": True})

    removal_set = overrides.removal_set if overrides else frozenset()
    keep_plural_set = overrides.keep_plural_set if overrides else frozenset()
    dropped_plural_acc: list[tuple[str, str]] = []
    allow_stale_overrides = (
        os.environ.get("ALLOW_STALE_OVERRIDES") == "1"
    )

    blocks = [
        _render_metadata_block(metadata_for_block),
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
        text, stripped_xrefs = auto_strip_xref(text, removal_set)
        if overrides.recapitalize_pairs:
            pairs = [tuple(p) for p in overrides.recapitalize_pairs]
            assert_letters_only(pairs)
            text = apply_recap_pairs(text, pairs)

    # Phase 9 — R9 whitespace post-emit text replace (mirrors markdown.py).
    if editorial_overrides is not None and editorial_overrides.R9_whitespace:
        text = apply_r9_whitespace(text, editorial_overrides.R9_whitespace)

    if curator_log is not None and overrides is not None:
        curator_log.setdefault("dangling_xrefs_stripped", []).extend(stripped_xrefs)
        curator_log.setdefault("dropped_plural_variants", []).extend(
            dropped_plural_acc
        )

    return text.encode("utf-8")
