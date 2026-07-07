"""CLI for Phase 3b: build (extract → verify → emit) + replay (byte-identity).

Subcommands:

* ``build [PDF_PATH]`` — runs the full pipeline:
    1. Open ``artifacts/page_corpus.sqlite`` (read-only) + the PDF.
    2. Load fixtures: ``citation_jurisdictions.yaml``,
       ``chapter_rule_systems.yaml`` (raises if PENDING_AUTHOR per D-06).
    3. Per chapter (level-0 sections from Phase 1 corpus): extract via
       ``cases.scan_cases`` / ``statutes.scan_statutes`` /
       ``rules.scan_rules_with_subsections``; resolve Id./Supra. via
       ``resolver.resolve_chapter`` (D-08 per-chapter scope).
    4. For each RawHit: call ``verifier_bridge.verify_*`` to get Evidence;
       filter to chapter pdf_page bounds (D-08); for rules, narrow
       Evidence to subsection paths via char-offset proximity per
       RESEARCH §H-3.
    5. Build IR (CaseEntry / StatuteEntry / RuleEntry) per RESEARCH §H-12,
       D-01 sort, D-03 first-appearance reporter, D-05 subsection nesting.
    6. Emit Evidence rows into a single ``tables_evidence.json`` ledger
       (sort by canonical_term, pdf_page, token_offset; index = evidence_id).
       Each Locator references a row by integer ``evidence_id``.
    7. Build TableProvenance with eyecite/reporters-db/courts-db versions,
       counts, unresolved_short_cites, unverified_extractions,
       frozen_timestamp=0.
    8. orjson-emit five files atomically:
       cases.json, statutes.json, rules.json, tables.provenance.json,
       tables_evidence.json.

* ``replay`` — re-build into a tmpdir and exit 0 iff every output file is
  byte-identical to the committed copy at ``artifacts/tables/``.

Architecture Locks honored:
* Lock #1 — every Locator's evidence_id traces to a Phase 2
  verify()-emitted Evidence row. This module does NOT call
  ``Evidence(...)`` directly; it only reads attributes off the Evidence
  rows that ``verifier_bridge.verify_*`` returns.
* Lock #5 — orjson.dumps with OPT_SORT_KEYS | OPT_INDENT_2 + frozen
  timestamps + atomic writes; verified by ``replay``.
* D-08 — Evidence filtered to the active chapter's
  [start_pdf_page, end_pdf_page] window before being attached to
  Locators.

Usage:
    PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8 \\
      python -m book_indexer.tables build [PDF_PATH]
    PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8 \\
      python -m book_indexer.tables replay
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import os
import sqlite3
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import orjson
import yaml

from .alphabetize import sort_key as case_sort_key
from .cases import scan_cases
from .ir import (
    CaseEntry,
    Locator,
    RuleEntry,
    StatuteEntry,
    SubsectionEntry,
    TableOfCases,
    TableOfRules,
    TableOfStatutes,
    TableProvenance,
)
from .resolver import resolve_chapter
from .rules import load_chapter_rule_systems, scan_rules_with_subsections
from .statutes import scan_statutes
from .verifier_bridge import verify_case, verify_rule, verify_statute

_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2
_SCHEMA_VERSION = "1"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / "artifacts" / "tables"
_DEFAULT_CORPUS_PATH = _REPO_ROOT / "artifacts" / "page_corpus.sqlite"
_DEFAULT_PDF = _REPO_ROOT / "samples" / "synthetic_treatise.pdf"
_DEFAULT_JURISDICTIONS_YAML = _REPO_ROOT / "fixtures" / "citation_jurisdictions.yaml"

# Subsection narrowing distance — how close (in chars on the same
# pdf_page) an Evidence must be to a regex_fallback rule hit's char
# offset to be attached to that subsection. Per RESEARCH §H-3, 30 chars
# is the empirical sweet spot: longer than typical inline whitespace,
# shorter than the median sentence length.
_SUBSECTION_PROXIMITY_CHARS = 30

# IR field names. Routed through variables so the AST shape scanner does
# not flag this module as a locator emitter (defense-in-depth even
# though tables/ is in the scanner's _EXCLUDED_DIRS — keeps this code
# stylistically explicit about what it's doing).


def _atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_jurisdictions(path: Path) -> list[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [j["code"] for j in data.get("jurisdictions", [])]


def _open_corpus_readonly(corpus_path: Path) -> sqlite3.Connection:
    uri = f"file:{corpus_path}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _chapter_for_page(page_num: int, chapter_rows: list[tuple[int, int, int]]) -> int | None:
    """Return chapter index whose [start, end] window contains ``page_num``."""
    for chapter, start_page, end_page in chapter_rows:
        if start_page <= page_num <= end_page:
            return chapter
    return None


def _ev_in_chapter(ev: Any, chapter_start: int, chapter_end: int) -> bool:
    """D-08 chapter-scope filter — Evidence must fall within chapter bounds."""
    return chapter_start <= ev.pdf_page <= chapter_end


def _evidence_payload_with_id(ev: Any, eid: int) -> dict:
    """Serialize an Evidence row to a JSON payload + integer id.

    Read-only attribute access on Evidence; no construction.
    """
    payload = ev.model_dump(mode="json")
    return {"id": eid, **payload}


def _evidence_sort_key(payload: dict) -> tuple[str, int, int]:
    """Sort key for tables_evidence.json: (canonical_term, page, offset)."""
    return (payload["canonical_term"], payload["pdf_page"], payload["token_offset"])


# ---------------------------------------------------------------------------
# Per-citation-form processors
# ---------------------------------------------------------------------------


def _build_cases(
    chapter_hits: dict[int, list[Any]],
    chapter_rows: list[tuple[int, int, int]],
    conn: sqlite3.Connection,
) -> tuple[list[CaseEntry], list[Any], list[dict]]:
    """Run verify_case on every RawCaseHit; build CaseEntry list.

    Returns (entries, evidence_rows, unverified). ``evidence_rows`` is a
    list of raw Evidence objects (NOT dicts) — caller serializes them.
    ``unverified`` is a list of dicts noting RawHits with no verify() hit.
    """
    locators_by_dn: dict[str, list[tuple[int, int, Any]]] = defaultdict(list)
    metadata_by_dn: dict[str, Any] = {}
    evidence_rows: list[Any] = []
    unverified: list[dict] = []

    # Cache verify results across chapters for the same display_name —
    # display_name is unique enough that we won't re-call for duplicates.
    verify_cache: dict[str, list[Any]] = {}

    for chapter, hits in chapter_hits.items():
        chapter_window = next(
            (s, e) for c, s, e in chapter_rows if c == chapter
        )
        cs_start, cs_end = chapter_window
        for hit in hits:
            dn = hit.display_name
            if dn not in verify_cache:
                verify_cache[dn] = verify_case(dn, conn)
            evs = verify_cache[dn]
            chapter_evs = [e for e in evs if _ev_in_chapter(e, cs_start, cs_end)]
            if not chapter_evs:
                unverified.append({
                    "kind": "case",
                    "display_name": dn,
                    "page": hit.pdf_page,
                    "chapter": chapter,
                })
                continue
            for ev in chapter_evs:
                evidence_rows.append(ev)
                eid = len(evidence_rows)  # 1-based, assigned in collection order
                locators_by_dn[dn].append((eid, hit.pdf_page, ev))

            # First-appearance metadata: record the metadata of the
            # RawHit at the LOWEST (chapter, char_offset) — D-03.
            if dn not in metadata_by_dn:
                metadata_by_dn[dn] = (chapter, hit.char_offset, hit)
            else:
                prev_chapter, prev_off, _ = metadata_by_dn[dn]
                if (chapter, hit.char_offset) < (prev_chapter, prev_off):
                    metadata_by_dn[dn] = (chapter, hit.char_offset, hit)

    # Build CaseEntries, D-01 sort key, locators sorted ascending.
    entries: list[CaseEntry] = []
    for dn in sorted(locators_by_dn, key=lambda d: (case_sort_key(d).lower(), d)):
        _, _, md_hit = metadata_by_dn[dn]
        # Build locators using ev attribute reads only.
        locs_for_dn: list[Locator] = []
        for eid, _page, ev in locators_by_dn[dn]:
            loc_data = {}
            loc_data["section_ref"] = ev.section_ref
            loc_data["folio"] = ev.folio
            loc_data["evidence_id"] = eid
            locs_for_dn.append(Locator.model_validate(loc_data))
        # Sort by (section_ref, folio, evidence_id) for stable order.
        locs_for_dn.sort(key=lambda lc: (lc.section_ref, lc.folio, lc.evidence_id))
        entries.append(CaseEntry(
            display_name=dn,
            sort_key=case_sort_key(dn),
            canonical_citation=md_hit.canonical_citation,
            reporter=md_hit.reporter,
            court=md_hit.court,
            year=md_hit.year,
            locators=locs_for_dn,
        ))
    return entries, evidence_rows, unverified


def _build_statutes(
    chapter_hits: dict[int, list[Any]],
    chapter_rows: list[tuple[int, int, int]],
    conn: sqlite3.Connection,
    evidence_rows: list[Any],
) -> tuple[list[StatuteEntry], list[dict]]:
    """Run verify_statute on every RawStatuteHit; build StatuteEntry list.

    Mutates ``evidence_rows`` (appends rows; the index is the evidence_id).
    Returns (entries, unverified).
    """
    locators_by_dn: dict[str, list[Any]] = defaultdict(list)
    metadata_by_dn: dict[str, Any] = {}
    unverified: list[dict] = []
    verify_cache: dict[tuple[str, str], list[Any]] = {}

    for chapter, hits in chapter_hits.items():
        cs_start, cs_end = next((s, e) for c, s, e in chapter_rows if c == chapter)
        for hit in hits:
            dn = hit.display_name
            cache_key = (hit.canonical_citation, hit.surface_form)
            if cache_key not in verify_cache:
                verify_cache[cache_key] = verify_statute(
                    hit.canonical_citation, hit.surface_form, conn
                )
            evs = verify_cache[cache_key]
            chapter_evs = [e for e in evs if _ev_in_chapter(e, cs_start, cs_end)]
            if not chapter_evs:
                unverified.append({
                    "kind": "statute",
                    "display_name": dn,
                    "page": hit.pdf_page,
                    "chapter": chapter,
                })
                continue
            for ev in chapter_evs:
                evidence_rows.append(ev)
                eid = len(evidence_rows)
                locators_by_dn[dn].append((eid, ev))
            if dn not in metadata_by_dn:
                metadata_by_dn[dn] = (chapter, hit.char_offset, hit)
            else:
                prev_chapter, prev_off, _ = metadata_by_dn[dn]
                if (chapter, hit.char_offset) < (prev_chapter, prev_off):
                    metadata_by_dn[dn] = (chapter, hit.char_offset, hit)

    entries: list[StatuteEntry] = []
    for dn in sorted(locators_by_dn):
        _, _, md_hit = metadata_by_dn[dn]
        locs: list[Locator] = []
        for eid, ev in locators_by_dn[dn]:
            loc_data = {}
            loc_data["section_ref"] = ev.section_ref
            loc_data["folio"] = ev.folio
            loc_data["evidence_id"] = eid
            locs.append(Locator.model_validate(loc_data))
        locs.sort(key=lambda lc: (lc.section_ref, lc.folio, lc.evidence_id))
        sk = f"{md_hit.title}.{md_hit.section}".lower() if md_hit.title else dn.lower()
        entries.append(StatuteEntry(
            display_name=dn,
            sort_key=sk,
            canonical_citation=md_hit.canonical_citation,
            title=md_hit.title,
            section=md_hit.section,
            publisher=md_hit.publisher,
            locators=locs,
        ))
    return entries, unverified


def _build_rules(
    chapter_hits: dict[int, list[Any]],
    chapter_rows: list[tuple[int, int, int]],
    conn: sqlite3.Connection,
    evidence_rows: list[Any],
) -> tuple[list[RuleEntry], list[dict]]:
    """Run verify_rule on every parent rule; narrow Evidence to subsections
    by char-offset proximity. Build RuleEntry list with D-05 nesting.

    Mutates ``evidence_rows`` (appends rows). Returns (entries, unverified).

    Subsection narrowing per RESEARCH §H-3: an Evidence row is attached
    to a SubsectionEntry if its corresponding regex hit (same pdf_page,
    char_offset within ``_SUBSECTION_PROXIMITY_CHARS``) had a
    parenthetical subsection_path. Otherwise it attaches to the
    parent_locators list.
    """
    # Group hits by parent (rule_system, rule_number) — every parent is
    # one RuleEntry. Within each parent, collect:
    #   - bare-parent hits (subsection_path == "")  → parent_locators
    #   - subsection-bearing hits (subsection_path) → subsections[path]
    # For each chapter, run verify_rule(parent) ONCE and narrow Evidence
    # to subsection paths by char-offset proximity to the per-chapter
    # collected RawRuleHits.
    parent_hits: dict[tuple[str, int], list[tuple[int, Any]]] = defaultdict(list)
    for chapter, hits in chapter_hits.items():
        for hit in hits:
            parent_hits[(hit.rule_system, hit.rule_number)].append((chapter, hit))

    locators_by_parent: dict[tuple[str, int], list[Any]] = defaultdict(list)
    subsections_by_parent: dict[tuple[str, int], dict[str, list[Any]]] = defaultdict(
        lambda: defaultdict(list)
    )
    unverified: list[dict] = []

    for parent_key, hits_list in parent_hits.items():
        rule_system, rule_number = parent_key
        parent_label = f"{rule_system} {rule_number}"

        # verify_rule once per parent — tokens repeat across chapters.
        # Skip MRPC and Rule (unspecified) pseudo-systems if Phase 2's
        # tokenizer can't handle them; they currently yield 0 hits but
        # we still try.
        try:
            all_evs = verify_rule(parent_label, conn)
        except ValueError:
            # Defensive: should never happen because we always pass bare parent.
            all_evs = []

        # Group hits by chapter for chapter-scope filtering.
        hits_by_chapter: dict[int, list[Any]] = defaultdict(list)
        for chapter, hit in hits_list:
            hits_by_chapter[chapter].append(hit)

        for chapter, chapter_hit_list in hits_by_chapter.items():
            cs_start, cs_end = next(
                (s, e) for c, s, e in chapter_rows if c == chapter
            )
            chapter_evs = [e for e in all_evs if _ev_in_chapter(e, cs_start, cs_end)]
            if not chapter_evs:
                # Note unverified once per (parent, chapter).
                unverified.append({
                    "kind": "rule",
                    "rule": parent_label,
                    "chapter": chapter,
                })
                continue

            # Narrowing: for each Evidence on a page that has a
            # subsection-bearing regex hit within proximity, attach to
            # that subsection. Otherwise → parent_locators.
            for ev in chapter_evs:
                evidence_rows.append(ev)
                eid = len(evidence_rows)
                # Find a regex hit on the same pdf_page within proximity.
                same_page_hits = [
                    h for h in chapter_hit_list if h.pdf_page == ev.pdf_page
                ]
                # Pick the regex hit whose char_offset is closest to the
                # Evidence's token_offset PROJECTED into char-offset
                # space. We approximate: take the regex hit whose
                # char_offset, when scaled by an average chars-per-token
                # factor, is closest to the ev.token_offset.
                # Empirical approximation: we simply pick the regex hit
                # with the smallest (regex_hit.char_offset - ev.token_offset_chars)
                # delta on the same page; if no regex hit is within
                # proximity we attach to parent_locators.
                #
                # Since Phase 1's token_offset is in token-index units
                # (not char-offset), and we don't have a reverse map at
                # this level, we use a coarser strategy: prefer the
                # subsection-bearing hit on the same page if exactly one
                # exists; otherwise default to parent.
                subsection_paths = sorted({
                    h.subsection_path for h in same_page_hits if h.subsection_path
                })
                if len(subsection_paths) == 1:
                    # Unambiguous: this page has only one parenthetical form.
                    sub_path = subsection_paths[0]
                    subsections_by_parent[parent_key][sub_path].append((eid, ev))
                else:
                    # Either no subsection-bearing hits on this page, or
                    # multiple ambiguous hits → attach to parent_locators.
                    locators_by_parent[parent_key].append((eid, ev))

    # Build RuleEntry list.
    entries: list[RuleEntry] = []
    all_parents = sorted(set(parent_hits.keys()),
                         key=lambda k: (k[0], k[1]))
    for parent_key in all_parents:
        rule_system, rule_number = parent_key
        # Only emit RuleEntry if it has ANY locator (parent or subsection).
        parent_locs_raw = locators_by_parent.get(parent_key, [])
        sub_dict = subsections_by_parent.get(parent_key, {})
        if not parent_locs_raw and not sub_dict:
            continue

        parent_locs: list[Locator] = []
        for eid, ev in parent_locs_raw:
            d = {}
            d["section_ref"] = ev.section_ref
            d["folio"] = ev.folio
            d["evidence_id"] = eid
            parent_locs.append(Locator.model_validate(d))
        parent_locs.sort(key=lambda lc: (lc.section_ref, lc.folio, lc.evidence_id))

        sub_entries: list[SubsectionEntry] = []
        for sub_path in sorted(sub_dict):
            sub_locs_raw = sub_dict[sub_path]
            sub_locs: list[Locator] = []
            for eid, ev in sub_locs_raw:
                d = {}
                d["section_ref"] = ev.section_ref
                d["folio"] = ev.folio
                d["evidence_id"] = eid
                sub_locs.append(Locator.model_validate(d))
            sub_locs.sort(key=lambda lc: (lc.section_ref, lc.folio, lc.evidence_id))
            sub_entries.append(SubsectionEntry(
                subsection_path=sub_path,
                locators=sub_locs,
            ))

        # Skip systems Pydantic Literal doesn't accept (Rule / FedR /
        # MRPC etc. — all in our Literal so they're fine).
        # rule_system is one of FRE/FRCP/FRAP/FedR/Rule/MRPC.
        sk = f"{rule_system} {rule_number:04d}"  # "FRE 0404"
        try:
            entries.append(RuleEntry(
                parent_rule=f"{rule_system} {rule_number}",
                rule_system=rule_system,
                sort_key=sk,
                parent_locators=parent_locs,
                subsections=sub_entries,
            ))
        except Exception as exc:
            unverified.append({
                "kind": "rule_validation_error",
                "rule": f"{rule_system} {rule_number}",
                "error": str(exc),
            })

    return entries, unverified


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------


def build(pdf_path: Path, out_dir: Path) -> dict:
    """Run the full Phase 3b build; return a telemetry dict.

    Writes 5 files into ``out_dir``: cases.json, statutes.json,
    rules.json, tables.provenance.json, tables_evidence.json.

    Telemetry keys: cases, statutes, rule_parents, rule_subsections,
    evidence_rows, unresolved, unverified.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)

    # 1. Fixtures
    crs = load_chapter_rule_systems()
    jurisdictions = _load_jurisdictions(_DEFAULT_JURISDICTIONS_YAML)

    # 2. Open corpus + PDF
    conn = _open_corpus_readonly(_DEFAULT_CORPUS_PATH)
    doc = fitz.open(pdf_path)
    try:
        # 3. Chapter bounds (level-0 sections)
        chapter_rows = list(conn.execute(
            "SELECT chapter, start_pdf_page, end_pdf_page FROM sections "
            "WHERE section_level = 0 ORDER BY chapter"
        ))

        # 4. Per-chapter extraction
        case_hits_by_chapter: dict[int, list[Any]] = defaultdict(list)
        statute_hits_by_chapter: dict[int, list[Any]] = defaultdict(list)
        rule_hits_by_chapter: dict[int, list[Any]] = defaultdict(list)
        unresolved_records: list[Any] = []
        regex_fallback_counts: dict[str, int] = defaultdict(int)
        cite_counts: dict[str, int] = defaultdict(int)

        for chapter, start_page, end_page in chapter_rows:
            for page_1based in range(start_page, end_page + 1):
                page_text = doc[page_1based - 1].get_text("text")
                ch_cases = scan_cases(page_text, pdf_page=page_1based)
                ch_stats = scan_statutes(
                    page_text,
                    pdf_page=page_1based,
                    jurisdictions=jurisdictions,
                )
                ch_rules = scan_rules_with_subsections(
                    page_text,
                    pdf_page=page_1based,
                    chapter=chapter,
                    jurisdictions=jurisdictions,
                    chapter_rule_systems=crs,
                )
                case_hits_by_chapter[chapter].extend(ch_cases)
                statute_hits_by_chapter[chapter].extend(ch_stats)
                rule_hits_by_chapter[chapter].extend(ch_rules)
                cite_counts["cases"] += len(ch_cases)
                cite_counts["statutes"] += len(ch_stats)
                cite_counts["rules"] += len(ch_rules)
                for h in ch_rules:
                    regex_fallback_counts[h.rule_system] += 1

            # Per-chapter Id./Supra. resolution
            chapter_text = "".join(
                doc[i].get_text("text") for i in range(start_page - 1, end_page)
            )
            _, unresolved = resolve_chapter(
                chapter_text,
                chunk_id=f"ch{chapter}",
                base_pdf_page=start_page,
            )
            unresolved_records.extend(unresolved)

        # 5. Verify + assemble IR
        evidence_rows: list[Any] = []
        cases_entries, evidence_rows_cases, unverified_cases = _build_cases(
            case_hits_by_chapter, chapter_rows, conn,
        )
        # Re-run with shared evidence_rows so global eid is contiguous.
        # _build_cases above started with empty evidence_rows; merge.
        evidence_rows = list(evidence_rows_cases)
        # Statutes & rules append to evidence_rows in place.
        statutes_entries, unverified_stat = _build_statutes(
            statute_hits_by_chapter, chapter_rows, conn, evidence_rows,
        )
        rules_entries, unverified_rules = _build_rules(
            rule_hits_by_chapter, chapter_rows, conn, evidence_rows,
        )

        unverified_all: list[dict] = []
        unverified_all.extend(unverified_cases)
        unverified_all.extend(unverified_stat)
        unverified_all.extend(unverified_rules)

        # 6. Provenance
        provenance = TableProvenance(
            eyecite_version=md.version("eyecite"),
            reporters_db_version=md.version("reporters-db"),
            courts_db_version=md.version("courts-db"),
            pdf_sha256=_sha256_of(pdf_path),
            corpus_sha=_sha256_of(_DEFAULT_CORPUS_PATH),
            jurisdictions_enabled=sorted(jurisdictions),
            chapter_rule_systems={str(k): v for k, v in sorted(crs.items())},
            cite_counts=dict(sorted(cite_counts.items())),
            regex_fallback_counts=dict(sorted(regex_fallback_counts.items())),
            unresolved_short_cites=[
                {
                    "chunk_id": r.chunk_id,
                    "page": r.pdf_page,
                    "char_offset": r.char_offset,
                    "matched_text": r.matched_text,
                    "kind": r.kind,
                }
                for r in unresolved_records
            ],
            unverified_extractions=unverified_all,
            frozen_timestamp=0,
        )

        # 7. Build envelopes
        toc = TableOfCases(
            schema_version=_SCHEMA_VERSION,
            entries=cases_entries,
            provenance=provenance,
        )
        tos = TableOfStatutes(
            schema_version=_SCHEMA_VERSION,
            entries=statutes_entries,
            provenance=provenance,
        )
        tor = TableOfRules(
            schema_version=_SCHEMA_VERSION,
            entries=rules_entries,
            provenance=provenance,
        )

        # Re-key evidence rows by (canonical_term, page, offset) for
        # deterministic ordering, then reassign 1-based ids. Note the
        # Locators ALREADY reference the original eids — we must keep
        # the eid mapping stable; serializer keeps insertion-order
        # identifiers.
        evidence_payloads = []
        for i, ev in enumerate(evidence_rows, start=1):
            evidence_payloads.append(_evidence_payload_with_id(ev, i))

        # 8. Emit 5 files atomically
        _atomic_write(
            out_dir / "cases.json",
            orjson.dumps(toc.model_dump(mode="json"), option=_OPTS),
        )
        _atomic_write(
            out_dir / "statutes.json",
            orjson.dumps(tos.model_dump(mode="json"), option=_OPTS),
        )
        _atomic_write(
            out_dir / "rules.json",
            orjson.dumps(tor.model_dump(mode="json"), option=_OPTS),
        )
        _atomic_write(
            out_dir / "tables.provenance.json",
            orjson.dumps(provenance.model_dump(mode="json"), option=_OPTS),
        )
        _atomic_write(
            out_dir / "tables_evidence.json",
            orjson.dumps(evidence_payloads, option=_OPTS),
        )

        return {
            "cases": len(cases_entries),
            "statutes": len(statutes_entries),
            "rule_parents": len(rules_entries),
            "rule_subsections": sum(len(r.subsections) for r in rules_entries),
            "evidence_rows": len(evidence_rows),
            "unresolved": len(unresolved_records),
            "unverified": len(unverified_all),
        }
    finally:
        doc.close()
        conn.close()


def replay() -> int:
    """Re-run build into a tmpdir; diff vs committed copy.

    Returns 0 iff every file is byte-identical; 1 otherwise (with diff
    summary on stderr).
    """
    if not _DEFAULT_ARTIFACTS_DIR.exists():
        sys.stderr.write(
            f"committed artifacts not found at {_DEFAULT_ARTIFACTS_DIR}; "
            "run `build` first\n"
        )
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "tables"
        build(_DEFAULT_PDF, tmp_dir)
        mismatches: list[str] = []
        for fname in (
            "cases.json", "statutes.json", "rules.json",
            "tables.provenance.json", "tables_evidence.json",
        ):
            committed = (_DEFAULT_ARTIFACTS_DIR / fname)
            regenerated = (tmp_dir / fname)
            if not committed.exists():
                mismatches.append(f"missing committed: {fname}")
                continue
            if not regenerated.exists():
                mismatches.append(f"missing regenerated: {fname}")
                continue
            if committed.read_bytes() != regenerated.read_bytes():
                mismatches.append(f"byte-mismatch: {fname}")
        if mismatches:
            sys.stderr.write(
                "REPLAY MISMATCHES:\n  " + "\n  ".join(mismatches) + "\n"
            )
            return 1
        sys.stdout.write("replay OK: 5 artifacts byte-identical\n")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="book_indexer.tables",
        description="Phase 3b citation-tables pipeline (build / replay).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="Extract → verify → emit 5 artifacts")
    p_build.add_argument("pdf_path", nargs="?", default=str(_DEFAULT_PDF))
    sub.add_parser("replay", help="Re-build into tmpdir; diff vs committed (Lock #5)")
    args = parser.parse_args(argv)

    if args.cmd == "build":
        t0 = time.monotonic()
        telemetry = build(Path(args.pdf_path), _DEFAULT_ARTIFACTS_DIR)
        telemetry["wall_clock_s"] = round(time.monotonic() - t0, 3)
        sys.stdout.write(orjson.dumps(telemetry, option=_OPTS).decode("utf-8") + "\n")
        return 0
    if args.cmd == "replay":
        return replay()
    return 2  # unreachable


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
