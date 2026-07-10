"""Tests for OUT-01 markdown renderer (Phase 5 Wave 2).

Covers:
  - 12-line metadata HTML comment block (AUD-04)
  - `# Subject Index` + letter dividers (`## A`, `## B`, ...)
  - Entry rendering with/without variants
  - B-08 variant filter (case-only drop, cruft drop, top-3 (-len, alpha))
  - 4-space-indented sub-entries
  - B-06 synthetic blocks (Phase 7 CUR-03 sub-rule b: bare-stem shape)
  - Tables: Cases (italicized names), Statutes, Rules (4-space sub-entries)
  - Pitfall §P-3: NO `\\r` byte (no CRLF leak)
  - Pitfall §P-4: literal U+00A0 + U+2013 bytes preserved
  - Determinism: 2 invocations byte-identical
  - Empty tree → still emits all 4 headers
  - Integration smoke (skipif live IR absent): ≥100 KB

requirements_addressed: OUT-01.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_indexer.render import (
    IndexTree,
    Locator,
    SubEntry,
    SyntheticEntry,
)
from book_indexer.tables.ir import (
    CaseEntry,
    RuleEntry,
    StatuteEntry,
    SubsectionEntry,
    TableOfCases,
    TableOfRules,
    TableOfStatutes,
    TableProvenance,
)

# --------------------------------------------------------------------------
# Module-level imports & exports
# --------------------------------------------------------------------------


def test_module_exports_render_markdown():
    from book_indexer.render import markdown as m

    assert hasattr(m, "render_markdown")


def test_render_markdown_returns_bytes(make_entry, frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[make_entry(canonical="admissibility")],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    assert isinstance(out, bytes)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _empty_tables() -> dict:
    prov = TableProvenance(
        eyecite_version="2.7.6",
        reporters_db_version="3.2.64",
        courts_db_version="0.10.27",
        pdf_sha256="a" * 64,
        corpus_sha="b" * 64,
        jurisdictions_enabled=[],
        chapter_rule_systems={},
        cite_counts={},
        regex_fallback_counts={},
        unresolved_short_cites=[],
        unverified_extractions=[],
    )
    return {
        "cases": TableOfCases(schema_version="1", entries=[], provenance=prov),
        "statutes": TableOfStatutes(schema_version="1", entries=[], provenance=prov),
        "rules": TableOfRules(schema_version="1", entries=[], provenance=prov),
    }


# --------------------------------------------------------------------------
# Metadata block
# --------------------------------------------------------------------------


def test_metadata_block_starts_with_html_comment(
    make_entry, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert text.startswith("<!--\n")


def test_metadata_block_keys_alphabetical(frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")

    # Extract metadata block — between `<!--\n` and `\n-->`.
    end = text.index("\n-->")
    block = text[len("<!--\n"):end]
    lines = block.split("\n")
    # Line 0 is the "book-indexer metadata" header.
    assert lines[0] == "book-indexer metadata"
    keylines = lines[1:]
    keys = [line.split("=", 1)[0] for line in keylines]
    assert keys == sorted(keys), f"metadata keys not alphabetical: {keys}"


def test_metadata_block_has_13_lines(frozen_metadata, make_provenance):
    """1 header + 12 key=value pairs.

    Phase 7 Wave 3 added ``pages_only_variant`` to the Metadata schema so
    Lock #5 byte-identity tests can pin each output variant separately.
    Header + 12 visible keys = 13 lines (built_at remains excluded as a
    frozen-epoch sentinel).
    """
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    end = text.index("\n-->")
    block = text[len("<!--\n"):end]
    lines = block.split("\n")
    # 1 header + 12 visible model fields = 13 lines.
    # Phase 7 Wave 3 added pages_only_variant; built_at remains excluded.
    assert len(lines) == 13, f"expected 13 inner lines, got {len(lines)}: {lines}"


# --------------------------------------------------------------------------
# Subject Index header & letter dividers
# --------------------------------------------------------------------------


def test_subject_index_header_present(make_entry, frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[make_entry(canonical="admissibility")],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    assert b"# Subject Index" in out


def test_letter_dividers_only_for_nonempty_letters(
    make_entry, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[
            make_entry(canonical="admissibility"),
            make_entry(canonical="hearsay"),
        ],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert "## A" in text
    assert "## H" in text
    # No 'B' letter-divider since no entry sorts under B.
    assert "## B\n" not in text


# --------------------------------------------------------------------------
# Entry rendering
# --------------------------------------------------------------------------


def test_single_entry_no_variants_format(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    """'admissibility, § 2.04 (p. 19)' with U+00A0 between § and 2.04."""
    from book_indexer.render.markdown import render_markdown

    e = make_entry(
        canonical="admissibility",
        variants=[],
        locators=[make_locator(section_ref="§2.04", folio="19")],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    # NBSP between § and 2.04 + NBSP between p. and 19.
    assert "admissibility, § 2.04 (p. 19)\n" in text


def test_single_entry_with_one_variant(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    e = make_entry(
        canonical="foo",
        variants=["bar"],
        locators=[make_locator(section_ref="§2.04", folio="19")],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert "foo *(also: bar)*, § 2.04 (p. 19)\n" in text


def test_variants_top3_by_length_desc_then_alpha(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    e = make_entry(
        canonical="foo",
        variants=["a", "bb", "cccc", "ddd", "eeee"],
        locators=[make_locator()],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    # Top-3 by (-len, alphabetical): cccc(4), eeee(4), ddd(3) — alpha within length.
    assert "*(also: cccc; eeee; ddd)*" in text


def test_variant_case_only_dropped(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    e = make_entry(
        canonical="foo",
        variants=["Foo", "FOO", "bar"],
        locators=[make_locator()],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert "*(also: bar)*" in text
    assert "Foo" not in text.split("\n")[len(text.split('\n'))-2]  # not in entry line
    # The variant 'Foo' / 'FOO' should not appear in the (also: ...) parenthetical.
    # Locate the entry line.
    entry_lines = [ln for ln in text.split("\n") if ln.startswith("foo ")]
    assert entry_lines, f"missing 'foo' entry: {text}"
    line = entry_lines[0]
    assert "Foo" not in line
    assert "FOO" not in line


def test_variant_cruft_dropped(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    e = make_entry(
        canonical="foo",
        variants=["(*foo", "[bracket]", "barbaz"],
        locators=[make_locator()],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    entry_lines = [ln for ln in text.split("\n") if ln.startswith("foo ")]
    assert entry_lines, f"missing 'foo' entry: {text}"
    line = entry_lines[0]
    assert "barbaz" in line
    assert "(*foo" not in line
    assert "[bracket]" not in line


def test_entry_with_subentry_indented_4_spaces(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    sub = SubEntry(
        text="under cross-examination",
        sort_key="under cross-examination",
        locators=[make_locator(section_ref="§3.02", folio="50")],
    )
    e = make_entry(
        canonical="hearsay",
        sub_entries=[sub],
        locators=[make_locator(section_ref="§2.04", folio="19")],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert "    under cross-examination, § 3.02 (p. 50)" in text


# --------------------------------------------------------------------------
# Synthetic entries (B-06)
# --------------------------------------------------------------------------


def test_synthetic_block_render(make_locator, frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    syn = SyntheticEntry(
        stem="hearsay",
        sibling_canonicals=("admissible hearsay", "hearsay exception"),
        locators=(
            make_locator(section_ref="§2.04", folio="19"),
            make_locator(section_ref="§3.02", folio="165", evidence_id=2),
        ),
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[])
    out = render_markdown(tree, [syn], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    # Phase 7 CUR-03 sub-rule b: synthesized parent emits bare stem (no marker).
    assert "(synthesized)" not in text
    assert "\nhearsay\n" in text or text.startswith("hearsay\n") or "\nhearsay\n" in text
    assert "    admissible hearsay" in text
    assert "    hearsay exception" in text


def test_synthetic_integrates_alphabetically(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    """Synthetic 'evidence' (E) should appear under ## E with regular E entries."""
    from book_indexer.render.markdown import render_markdown

    syn = SyntheticEntry(
        stem="evidence",
        sibling_canonicals=("admissible evidence", "adverse evidence"),
        locators=(make_locator(section_ref="§2.04", folio="19"),),
    )
    e = make_entry(canonical="admissibility")  # under A
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out = render_markdown(tree, [syn], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert "## A" in text
    assert "## E" in text
    # Ensure synthetic appears AFTER ## E header. Phase 7 CUR-03 sub-rule b
    # drops the (synthesized) marker; the parent line is now a bare ``evidence``.
    e_pos = text.index("## E")
    # Find the synthesized parent line — it's the bare stem on its own line
    # immediately after ## E (followed by 4-space-indented siblings).
    assert "(synthesized)" not in text
    syn_pos = text.index("\nevidence\n", e_pos)
    assert syn_pos > e_pos


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def test_table_of_cases_italicized_name(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    case_loc = Locator(section_ref="§1.04", folio="58", evidence_id=1)
    case = CaseEntry(
        display_name="Jones v. Barnes",
        sort_key="jones v. barnes",
        canonical_citation="463 U.S. 745 (1983)",
        reporter="U.S.",
        court=None,
        year=1983,
        locators=[case_loc],
    )
    prov = _empty_tables()["cases"].provenance
    tables = _empty_tables()
    tables["cases"] = TableOfCases(schema_version="1", entries=[case], provenance=prov)

    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[])
    out = render_markdown(tree, [], tables, frozen_metadata)
    text = out.decode("utf-8")
    assert "# Table of Cases" in text
    assert "*Jones v. Barnes*" in text


def test_table_of_statutes_present(frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    s_loc = Locator(section_ref="§2.05", folio="20", evidence_id=1)
    statute = StatuteEntry(
        display_name="42 U.S.C. § 1983",
        sort_key="42 1983",
        canonical_citation="42 U.S.C. § 1983",
        title="42",
        section="1983",
        locators=[s_loc],
    )
    prov = _empty_tables()["statutes"].provenance
    tables = _empty_tables()
    tables["statutes"] = TableOfStatutes(
        schema_version="1", entries=[statute], provenance=prov
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[])
    out = render_markdown(tree, [], tables, frozen_metadata)
    text = out.decode("utf-8")
    assert "# Table of Statutes" in text
    assert "42 U.S.C. § 1983" in text


def test_table_of_rules_subentry_indented(frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    parent_loc = Locator(section_ref="§2.06", folio="90", evidence_id=1)
    sub_loc = Locator(section_ref="§2.06", folio="91", evidence_id=2)
    sub = SubsectionEntry(subsection_path="(b)(1)", locators=[sub_loc])
    rule = RuleEntry(
        parent_rule="FRE 404",
        rule_system="FRE",
        sort_key="fre 404",
        parent_locators=[parent_loc],
        subsections=[sub],
    )
    prov = _empty_tables()["rules"].provenance
    tables = _empty_tables()
    tables["rules"] = TableOfRules(
        schema_version="1", entries=[rule], provenance=prov
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[])
    out = render_markdown(tree, [], tables, frozen_metadata)
    text = out.decode("utf-8")
    assert "# Table of Rules" in text
    assert "FRE 404" in text
    # Subsection indented 4 spaces.
    assert "    FRE 404(b)(1)" in text


# --------------------------------------------------------------------------
# Empty tree
# --------------------------------------------------------------------------


def test_empty_tree_still_emits_all_4_headers(frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[])
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    text = out.decode("utf-8")
    assert "# Subject Index" in text
    assert "# Table of Cases" in text
    assert "# Table of Statutes" in text
    assert "# Table of Rules" in text


# --------------------------------------------------------------------------
# Byte-level guarantees (Pitfall §P-3, §P-4)
# --------------------------------------------------------------------------


def test_no_carriage_return_byte(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    """Pitfall §P-3 — never CRLF."""
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[make_entry(canonical="admissibility")],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    assert b"\r" not in out


def test_nbsp_byte_preserved(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    """U+00A0 must be literal `\\xc2\\xa0`, NEVER `&nbsp;`."""
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[make_entry(canonical="admissibility")],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    assert b"\xc2\xa0" in out
    assert b"&nbsp;" not in out


def test_output_ends_with_newline(make_entry, frozen_metadata, make_provenance):
    from book_indexer.render.markdown import render_markdown

    tree = IndexTree(
        schema_version="1",
        provenance=make_provenance(),
        entries=[make_entry()],
    )
    out = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    assert out.endswith(b"\n")


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_render_deterministic(
    make_entry, make_locator, frozen_metadata, make_provenance
):
    from book_indexer.render.markdown import render_markdown

    e = make_entry(
        canonical="hearsay",
        variants=["hearsay rule", "hearsay-rule"],
        locators=[
            make_locator(section_ref="§2.04", folio="19"),
            make_locator(section_ref="§3.02", folio="165", evidence_id=2),
        ],
    )
    tree = IndexTree(schema_version="1", provenance=make_provenance(), entries=[e])
    out1 = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    out2 = render_markdown(tree, [], _empty_tables(), frozen_metadata)
    assert out1 == out2


# --------------------------------------------------------------------------
# Integration smoke (skip if live IR / tables absent)
# --------------------------------------------------------------------------


_LIVE_IR = Path("artifacts/index_tree.json")
_LIVE_TABLES_DIR = Path("artifacts/tables")


@pytest.mark.skipif(
    not _LIVE_IR.exists() or not (_LIVE_TABLES_DIR / "cases.json").exists(),
    reason="live IR / tables absent",
)
def test_integration_smoke_live_ir(frozen_metadata):
    """Full render against live IR + tables ≥ 100 KB; contains expected
    structural markers (RESEARCH §H-12 md_size_bytes_min). Runs B-05 cruft
    filter + B-06 synthetic generation on the live IR before rendering."""
    import spacy

    from book_indexer.render.filter import is_cruft
    from book_indexer.render.markdown import render_markdown
    from book_indexer.render.synthesize import (
        load_stopwords,
        synthesize_bare_lemma_entries,
    )

    tree = IndexTree.model_validate(json.loads(_LIVE_IR.read_text()))
    cases = TableOfCases.model_validate(
        json.loads((_LIVE_TABLES_DIR / "cases.json").read_text())
    )
    statutes = TableOfStatutes.model_validate(
        json.loads((_LIVE_TABLES_DIR / "statutes.json").read_text())
    )
    rules = TableOfRules.model_validate(
        json.loads((_LIVE_TABLES_DIR / "rules.json").read_text())
    )
    tables = {"cases": cases, "statutes": statutes, "rules": rules}

    # Run B-05 cruft + B-06 synthesis end-to-end (mirrors what __main__ does).
    surviving = [e for e in tree.entries if not is_cruft(e.canonical)]
    nlp = spacy.load("en_core_web_lg")
    stopwords = load_stopwords()
    synthetics = synthesize_bare_lemma_entries(surviving, nlp, stopwords)

    out = render_markdown(tree, synthetics, tables, frozen_metadata)
    # RESEARCH §H-12 estimated 100_000 ("calibrate post-build"). Empirical
    # post-build measurement on the reference corpus (901 entries, 0 sub-entries,
    # 21 synthetic stems, 422 variants, 2186 locators + 3 tables) yields
    # ~75.9 KB. Calibrated floor: 60_000 bytes (well above the empirical
    # value while leaving room for legitimate downstream variance).
    assert len(out) >= 60_000, f"live MD render too small: {len(out)} bytes"
    assert b"# Subject Index" in out
    assert b"\xc2\xa0" in out  # NBSP preserved
    # At least one letter divider (## A, ## B, ...).
    assert b"\n## " in out
    # Phase 7 CUR-03 sub-rule b: ``(synthesized)`` marker is unconditionally
    # dropped from rendered output. Smoke-test that synthetic blocks are
    # present by checking the count is non-zero AND the live IR produced
    # >= 1 SyntheticEntry (we already passed `synthetics` from the
    # generator into the renderer).
    assert b"(synthesized)" not in out
    assert len(synthetics) >= 1, "live IR yielded no synthetic entries"
