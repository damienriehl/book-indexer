"""CLI for Phase 5: build (compose render+audit pipeline) + replay.

Subcommands:

* ``build`` — runs the full Phase 5 pipeline:
    1. Pre-flight env (PYTHONHASHSEED=0, TZ=UTC, LC_ALL=C.UTF-8) +
       inputs (index_tree*.json, page_corpus.sqlite, coverage.draft.md,
       tables/{cases,statutes,rules}.json).
    2. Load IndexTree + table envelopes via Pydantic (extra='forbid').
    3. Apply B-05 cruft filter (filter.is_cruft) at compose time
       (renderers also filter; this is for telemetry / coverage only).
    4. Load spaCy en_core_web_lg + load fixtures/render_stopwords.yaml;
       call synthesize_bare_lemma_entries → list[SyntheticEntry].
    5. Build AUD-04 Metadata via build_metadata().
    6. Build audit bundle (page_corpus.txt + sections.json +
       index_evidence.json) via build_audit_bundle().
    7. Render markdown bytes via render_markdown().
    8. Render docx (frozen) via render_docx() → tmpfile, then move.
    9. Extend coverage.draft.md → final coverage.md via
       extend_coverage_report().
   10. Emit metadata.json sidecar via orjson OPT_SORT_KEYS|OPT_INDENT_2.
   11. Atomically write all 7 files to artifacts/render/* +
       artifacts/audit/*.
   12. Print telemetry JSON to stdout.

* ``replay`` — re-build into a tmpdir; diff every committed file
  against tmpdir copy. Exit 0 iff byte-identical for ALL 7 files.

Architecture Locks honored:
  - Lock #1: never calls verify(); never constructs Evidence directly.
  - Lock #5: orjson OPT_SORT_KEYS|OPT_INDENT_2; frozen Unix-epoch
    timestamps in metadata.built_at; LF-only bytes; atomic writes.

Per CONTEXT 05 D-06: v1.0 ships full bundle (no per-format flags).

OUT-03 (typeset PDF) is DEFERRED to v1.x per CONTEXT 05 D-01 (TeX Live
+ upmendex install footprint disproportionate for v1.0). The 6
shipped deliverables: index.md + index.docx + page_corpus.txt +
sections.json + index_evidence.json + coverage.md + metadata.json.

Usage::

    PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8 \\
      python -m book_indexer.render build
    PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8 \\
      python -m book_indexer.render replay
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import orjson

from book_indexer.tables.ir import (
    TableOfCases,
    TableOfRules,
    TableOfStatutes,
)

from book_indexer.curator import CuratorFixtureError, load_curator_overrides
from book_indexer.curator.fixture import load_editorial_overrides

from . import IndexTree
from .audit import build_audit_bundle
from .coverage import extend_coverage_report
from .docx import render_docx
from .docx_sections_only import render_docx_sections_only
from .errors import FreezeError, MetadataValidationError, RenderError
from .filter import is_cruft
from .markdown import render_markdown
from .markdown_sections_only import render_markdown_sections_only
from .metadata import build_metadata
from .synthesize import load_stopwords, synthesize_bare_lemma_entries

_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ARTIFACTS_DIR = _REPO_ROOT / "artifacts"
_RENDER_DIR = _ARTIFACTS_DIR / "render"
_AUDIT_DIR = _ARTIFACTS_DIR / "audit"

_INDEX_TREE_PATH = _ARTIFACTS_DIR / "index_tree.json"
_INDEX_TREE_PROV_PATH = _ARTIFACTS_DIR / "index_tree.provenance.json"
_INDEX_TREE_EVIDENCE_PATH = _ARTIFACTS_DIR / "index_tree_evidence.json"
_PAGE_CORPUS_PATH = _ARTIFACTS_DIR / "page_corpus.sqlite"
_COVERAGE_DRAFT_PATH = _ARTIFACTS_DIR / "coverage.draft.md"
_TABLES_DIR = _ARTIFACTS_DIR / "tables"
_TABLE_CASES_PATH = _TABLES_DIR / "cases.json"
_TABLE_STATUTES_PATH = _TABLES_DIR / "statutes.json"
_TABLE_RULES_PATH = _TABLES_DIR / "rules.json"

# Phase 7 Wave 3 — curator fixture (gates the build per CONTEXT D-08).
_CURATOR_FIXTURE_PATH = _REPO_ROOT / "fixtures" / "index_curator_overrides.yaml"

# Phase 9 Wave 2 — editorial-overrides fixture (gates the build per
# Phase 9 CONTEXT D-08). Loaded only when the file exists; sentinel rejection
# is fail-closed at the loader boundary (CuratorFixtureError raised on
# PENDING_AUTHOR; build halts cleanly).
_EDITORIAL_OVERRIDES_FIXTURE_PATH = (
    _REPO_ROOT / "fixtures" / "index_editorial_overrides.yaml"
)

# The 9 deliverables produced atomically by build (CONTEXT D-06 +
# RESEARCH §H-12 + Phase 7 OUT-05). OUT-03 (typeset PDF) is DEFERRED to v1.x.
# Phase 7 added 2 new render deliverables (sections-only md + docx).
_RENDER_OUTPUTS: tuple[tuple[str, str], ...] = (
    ("render", "index.md"),
    ("render", "index.docx"),
    ("render", "index_sections_only.md"),
    ("render", "index_sections_only.docx"),
    ("audit", "page_corpus.txt"),
    ("audit", "sections.json"),
    ("audit", "index_evidence.json"),
    ("audit", "coverage.md"),
    ("audit", "metadata.json"),
)


# ---------------------------------------------------------------------------
# Preflight (mirror assembly/__main__.py:_env_preflight verbatim)
# ---------------------------------------------------------------------------


def _env_preflight() -> None:
    """Determinism preflight (Lock #5).

    Raises ``RuntimeError`` on env-var mismatch (caller maps to exit 1).
    """
    for var, expected in (
        ("PYTHONHASHSEED", "0"),
        ("TZ", "UTC"),
        ("LC_ALL", "C.UTF-8"),
    ):
        actual = os.environ.get(var)
        if actual != expected:
            raise RuntimeError(
                f"environment determinism violated: {var}={actual!r} "
                f"(expected {expected!r}). Run with: env PYTHONHASHSEED=0 "
                f"TZ=UTC LC_ALL=C.UTF-8 python -m book_indexer.render ..."
            )


def _input_preflight() -> None:
    """Validate that all required input artifacts exist.

    Raises ``RuntimeError`` on the first missing input.
    """
    required: tuple[tuple[Path, str], ...] = (
        (_INDEX_TREE_PATH, "python -m book_indexer.assembly build"),
        (_INDEX_TREE_PROV_PATH, "python -m book_indexer.assembly build"),
        (_INDEX_TREE_EVIDENCE_PATH, "python -m book_indexer.assembly build"),
        (_PAGE_CORPUS_PATH, "python -m book_indexer.ingest build"),
        (_COVERAGE_DRAFT_PATH, "python -m book_indexer.assembly build"),
        (_TABLE_CASES_PATH, "python -m book_indexer.tables build"),
        (_TABLE_STATUTES_PATH, "python -m book_indexer.tables build"),
        (_TABLE_RULES_PATH, "python -m book_indexer.tables build"),
    )
    for path, recovery_cmd in required:
        if not path.exists():
            raise RuntimeError(
                f"missing input {path} — run upstream: {recovery_cmd}"
            )


# ---------------------------------------------------------------------------
# Atomic writes (mirror assembly/__main__.py:_atomic_write verbatim)
# ---------------------------------------------------------------------------


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` atomically (tmp + os.replace)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _load_table_or_none(path: Path, model_cls):
    """Load a table envelope JSON (or return None on missing)."""
    if not path.exists():
        return None
    return model_cls.model_validate_json(path.read_bytes())


def _build(
    artifacts_dir: Path,
    render_dir: Path,
    audit_dir: Path,
) -> dict:
    """Run the full Phase 5 build into render_dir + audit_dir.

    Returns a telemetry dict for stdout emission.
    """
    _input_preflight()

    t_start = time.monotonic()

    # 1. Load IR + tables (Pydantic validation = extra='forbid' guard).
    tree = IndexTree.model_validate_json(_INDEX_TREE_PATH.read_bytes())
    tables = {
        "cases": _load_table_or_none(_TABLE_CASES_PATH, TableOfCases),
        "statutes": _load_table_or_none(_TABLE_STATUTES_PATH, TableOfStatutes),
        "rules": _load_table_or_none(_TABLE_RULES_PATH, TableOfRules),
    }

    # 2. B-05 cruft filter (compose-time list for telemetry + coverage;
    # renderers re-filter independently — both call is_cruft so single
    # source of truth holds).
    b05_drops: list[str] = [
        e.canonical for e in tree.entries if is_cruft(e.canonical)
    ]
    surviving_entries = [e for e in tree.entries if not is_cruft(e.canonical)]

    # 3. B-06 synthesize via spaCy (lazy import so smoke tests without
    # spaCy still get past Pydantic validation).
    #
    # B-10 (CONTEXT 06 D-02; Plan 06-02 cross-callsite patch):
    # attach_phrase_overrides_to_meta populates nlp.meta so
    # synthesize_bare_lemma_entries skips union-of-token-lemmas
    # decomposition for curated hyphenated terms (cross-examination,
    # post-trial, etc.) — without it the default tokenizer fragments
    # them and inflates spurious 'cross' / 'examination' synthetic
    # clusters.
    import spacy  # type: ignore[import-not-found]

    from book_indexer.ingest.tokenizer import attach_phrase_overrides_to_meta

    nlp = spacy.load("en_core_web_lg")
    attach_phrase_overrides_to_meta(nlp)
    stopwords = load_stopwords()
    synthetics = synthesize_bare_lemma_entries(surviving_entries, nlp, stopwords)

    # 4. AUD-04 metadata.
    metadata = build_metadata()

    # 5. AUD-01 + AUD-02 audit bundle.
    audit_bundle = build_audit_bundle(_PAGE_CORPUS_PATH, _INDEX_TREE_EVIDENCE_PATH)

    # 5.5. Phase 7 — load curator overrides (CONTEXT D-08 build gate).
    # The fixture's metadata.curated_by gate hard-fails on PENDING_AUTHOR;
    # we let the CuratorFixtureError propagate so main() can format it.
    overrides = None
    if _CURATOR_FIXTURE_PATH.exists():
        overrides = load_curator_overrides(_CURATOR_FIXTURE_PATH)

    # Phase 9 — editorial-overrides fixture. Loader raises
    # CuratorFixtureError on PENDING_AUTHOR sentinel (Wave 1 default until
    # Wave 3 author-checkpoint signs it off). When the fixture file exists
    # but is unsigned the build halts here; until Wave 3 ships, treat the
    # absent-file path AND the sentinel-error path as a no-op apply pass —
    # editorial_overrides=None preserves v1.2.x behavior exactly.
    editorial_overrides = None
    if _EDITORIAL_OVERRIDES_FIXTURE_PATH.exists():
        try:
            editorial_overrides = load_editorial_overrides(
                _EDITORIAL_OVERRIDES_FIXTURE_PATH
            )
        except CuratorFixtureError as exc:
            # Wave 1 ships the fixture with PENDING_AUTHOR; until Wave 3
            # signoff, the fixture is intentionally unsigned. Skip the apply
            # pass with a stderr note so the renderer remains backward-
            # compatible (no apply on PENDING_AUTHOR; no behavior change vs
            # v1.2.x). Wave 3+ will halt on this branch by routing the
            # sentinel error through main()'s formatter.
            if "PENDING_AUTHOR" in str(exc):
                print(
                    f"[render] editorial-overrides fixture unsigned "
                    f"(PENDING_AUTHOR); skipping Phase 9 apply pass "
                    f"(Wave 3 author-checkpoint pending).",
                    file=sys.stderr,
                )
                editorial_overrides = None
            else:
                raise

    curator_log: dict[str, list] = {
        "dangling_xrefs_stripped": [],
        "dropped_plural_variants": [],
    }

    render_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    # 6. Render markdown (sections+pages variant — index.md).
    t_md_start = time.monotonic()
    md_bytes = render_markdown(
        tree, synthetics, tables, metadata,
        overrides=overrides,
        editorial_overrides=editorial_overrides,
        curator_log=curator_log,
    )
    md_wall = time.monotonic() - t_md_start

    # 7. Render docx (sections+pages variant — index.docx). render_docx
    # writes the FROZEN output directly to out_path via freeze_docx's
    # tmp+replace; no post-shuffle needed.
    docx_target = render_dir / "index.docx"
    t_dx_start = time.monotonic()
    render_docx(
        tree, synthetics, tables, metadata, docx_target,
        overrides=overrides,
        editorial_overrides=editorial_overrides,
        curator_log=curator_log,
    )
    docx_wall = time.monotonic() - t_dx_start

    # 7.5. Phase 7 OUT-05 — sections-only render variants.
    md_sections_target = render_dir / "index_sections_only.md"
    docx_sections_target = render_dir / "index_sections_only.docx"

    md_sections_bytes = render_markdown_sections_only(
        tree, synthetics, tables, metadata,
        overrides=overrides,
        editorial_overrides=editorial_overrides,
        curator_log=curator_log,
    )
    render_docx_sections_only(
        tree, synthetics, tables, metadata, docx_sections_target,
        overrides=overrides,
        editorial_overrides=editorial_overrides,
        curator_log=curator_log,
    )

    # 8. Extend coverage report (AUD-03).
    sections_payload = orjson.loads(audit_bundle["sections.json"])
    evidence_ledger = orjson.loads(
        _INDEX_TREE_EVIDENCE_PATH.read_bytes()
    ).get("entries", [])
    phase4_provenance = orjson.loads(_INDEX_TREE_PROV_PATH.read_bytes())

    # Range-collapse total — vacuous on the reference corpus per RESEARCH §H-6
    # (Phase 4 cite-rule already coalesces multi-folio occurrences).
    range_collapses_total = 0

    t_cov_start = time.monotonic()
    render_metrics = {
        "wall_clock_s": round(time.monotonic() - t_start, 3),
        "render_markdown_s": round(md_wall, 3),
        "render_docx_s": round(docx_wall, 3),
    }
    coverage_bytes = extend_coverage_report(
        draft_md=_COVERAGE_DRAFT_PATH.read_bytes(),
        tree=tree,
        evidence_ledger=evidence_ledger,
        sections_payload=sections_payload,
        render_metrics=render_metrics,
        b05_drops=b05_drops,
        b06_synthetics=synthetics,
        range_collapses_total=range_collapses_total,
        corpus_path=_PAGE_CORPUS_PATH,
        phase4_provenance=phase4_provenance,
        overrides=overrides,
        curator_log=curator_log,
    )
    cov_wall = time.monotonic() - t_cov_start

    # 9. Metadata.json sidecar.
    metadata_bytes = orjson.dumps(metadata.model_dump(), option=_OPTS)

    # 10. Atomic writes — all 9 files (Phase 7 added 2 sections-only render
    # deliverables).
    _atomic_write_bytes(render_dir / "index.md", md_bytes)
    _atomic_write_bytes(md_sections_target, md_sections_bytes)
    # docx outputs already at their targets (freeze_docx wrote them).
    _atomic_write_bytes(audit_dir / "page_corpus.txt", audit_bundle["page_corpus.txt"])
    _atomic_write_bytes(audit_dir / "sections.json", audit_bundle["sections.json"])
    _atomic_write_bytes(
        audit_dir / "index_evidence.json", audit_bundle["index_evidence.json"]
    )
    _atomic_write_bytes(audit_dir / "coverage.md", coverage_bytes)
    _atomic_write_bytes(audit_dir / "metadata.json", metadata_bytes)

    docx_size = (render_dir / "index.docx").stat().st_size
    docx_sections_size = docx_sections_target.stat().st_size
    md_size = len(md_bytes)
    md_sections_size = len(md_sections_bytes)
    return {
        "wall_clock_s": round(time.monotonic() - t_start, 3),
        "render_markdown_s": round(md_wall, 3),
        "render_docx_s": round(docx_wall, 3),
        "coverage_extend_s": round(cov_wall, 3),
        "entries_in_md": len(surviving_entries) + len(synthetics),
        "b05_drop_count": len(b05_drops),
        "b06_synthesize_count": len(synthetics),
        "range_collapses_total": range_collapses_total,
        "md_size_bytes": md_size,
        "docx_size_bytes": docx_size,
        "md_sections_size_bytes": md_sections_size,
        "docx_sections_size_bytes": docx_sections_size,
        "page_corpus_size_bytes": len(audit_bundle["page_corpus.txt"]),
        "sections_json_size_bytes": len(audit_bundle["sections.json"]),
        "index_evidence_size_bytes": len(audit_bundle["index_evidence.json"]),
        "coverage_md_size_bytes": len(coverage_bytes),
        "metadata_json_size_bytes": len(metadata_bytes),
        "curator_removals_applied": (
            len(overrides.removal_set) if overrides is not None else 0
        ),
        "curator_recap_pairs_applied": (
            len(overrides.recapitalize_pairs) if overrides is not None else 0
        ),
        "curator_dangling_xrefs_stripped": len(
            set(curator_log.get("dangling_xrefs_stripped", []))
        ),
        "curator_plural_variants_dropped": len(
            set(curator_log.get("dropped_plural_variants", []))
        ),
    }


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _replay() -> int:
    """Re-run build into a tmpdir; diff every output vs committed.

    Returns 0 iff every output is byte-identical; 1 otherwise.
    """
    if not all(
        (_ARTIFACTS_DIR / sub / name).exists() for sub, name in _RENDER_OUTPUTS
    ):
        sys.stderr.write(
            f"committed render artifacts not found at {_ARTIFACTS_DIR}/render"
            f"|audit; run `build` first\n"
        )
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        tmp_render = tmp_dir / "render"
        tmp_audit = tmp_dir / "audit"
        _build(artifacts_dir=_ARTIFACTS_DIR, render_dir=tmp_render, audit_dir=tmp_audit)

        mismatches: list[str] = []
        for sub, name in _RENDER_OUTPUTS:
            committed = _ARTIFACTS_DIR / sub / name
            regenerated = tmp_dir / sub / name
            if not committed.exists():
                mismatches.append(f"missing committed: {sub}/{name}")
                continue
            if not regenerated.exists():
                mismatches.append(f"missing regenerated: {sub}/{name}")
                continue
            if not filecmp.cmp(committed, regenerated, shallow=False):
                mismatches.append(f"byte-mismatch: {sub}/{name}")

        if mismatches:
            sys.stderr.write(
                "REPLAY MISMATCHES:\n  " + "\n  ".join(mismatches) + "\n"
            )
            return 1
        sys.stdout.write(
            f"replay OK: {len(_RENDER_OUTPUTS)} artifacts byte-identical\n"
        )
        return 0


# ---------------------------------------------------------------------------
# CLI dispatch
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="book_indexer.render",
        description=(
            "Phase 5 + Phase 7 rendering & audit-artifact pipeline (build / "
            "replay). Produces 8 deliverables + metadata.json sidecar "
            "atomically (4 render + 4 audit + 1 sidecar = 9 files). OUT-03 "
            "typeset PDF DEFERRED to v1.x per CONTEXT 05 D-01."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="Compose pipeline → 9 render+audit artifacts")
    sub.add_parser("replay", help="Re-build into tmpdir; diff vs committed (Lock #5)")
    args = parser.parse_args(argv)

    try:
        _env_preflight()
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    if args.cmd == "build":
        try:
            telemetry = _build(
                artifacts_dir=_ARTIFACTS_DIR,
                render_dir=_RENDER_DIR,
                audit_dir=_AUDIT_DIR,
            )
        except (
            RuntimeError, RenderError, FreezeError,
            MetadataValidationError, CuratorFixtureError,
        ) as exc:
            sys.stderr.write(f"ERROR: {exc}\n")
            return 1
        sys.stdout.write(orjson.dumps(telemetry, option=_OPTS).decode("utf-8") + "\n")
        return 0
    if args.cmd == "replay":
        return _replay()
    return 2  # unreachable; argparse required=True forbids bare cmd


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
