"""CLI for Phase 4: build (compose pipeline → index_tree.json) + replay.

Subcommands:

* ``build [PDF_PATH]`` — runs the full Phase 4 assembly pipeline:
    1. Pre-flight env (PYTHONHASHSEED=0, TZ=UTC, LC_ALL=C.UTF-8) +
       inputs (corpus, concepts/, tables/, acronym_overrides.yaml).
    2. Load spaCy ``en_core_web_lg`` + EntityRuler from
       ``fixtures/doctrinal_patterns.yaml``.
    3. Call ``tree.build_index_tree`` → ``(IndexTree, evidence_ledger)``.
    4. orjson-emit (OPT_SORT_KEYS + OPT_INDENT_2) atomically:
         - artifacts/index_tree.json
         - artifacts/index_tree.provenance.json
         - artifacts/index_tree_evidence.json
    5. coverage.emit_draft_report → artifacts/coverage.draft.md.
    6. Print telemetry JSON to stdout.

* ``replay`` — re-build into a tmpdir and exit 0 IFF every output file
  is byte-identical to the committed copy under ``artifacts/`` (Lock #5).

Architecture Locks honored:
* Lock #1 — every Locator.evidence_id traces to a verifier_sweep-produced
  Evidence row. This module does NOT call ``verify()`` directly nor
  construct ``Evidence(...)``; it threads through tree.build_index_tree.
* Lock #5 — orjson.dumps with OPT_SORT_KEYS | OPT_INDENT_2 + frozen
  timestamps + atomic writes; verified by ``replay``.

Usage:
    PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8 \\
      python -m book_indexer.assembly build [PDF_PATH]
    PYTHONHASHSEED=0 TZ=UTC LC_ALL=C.UTF-8 \\
      python -m book_indexer.assembly replay
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

import orjson
import yaml

from .coverage import emit_draft_report
from .tree import build_index_tree

_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_ARTIFACTS_DIR = _REPO_ROOT / "artifacts"
_DEFAULT_CONCEPTS_DIR = _REPO_ROOT / "artifacts" / "concepts"
_DEFAULT_TABLES_DIR = _REPO_ROOT / "artifacts" / "tables"
_DEFAULT_CORPUS_PATH = _REPO_ROOT / "artifacts" / "page_corpus.sqlite"
_DEFAULT_PDF = _REPO_ROOT / "samples" / "synthetic_treatise.pdf"
_DEFAULT_ACRONYMS = _REPO_ROOT / "fixtures" / "acronym_overrides.yaml"
_DEFAULT_PATTERNS = _REPO_ROOT / "fixtures" / "doctrinal_patterns.yaml"

_INDEX_TREE_PATH = _DEFAULT_ARTIFACTS_DIR / "index_tree.json"
_INDEX_TREE_PROV_PATH = _DEFAULT_ARTIFACTS_DIR / "index_tree.provenance.json"
_INDEX_TREE_EVIDENCE_PATH = _DEFAULT_ARTIFACTS_DIR / "index_tree_evidence.json"
_COVERAGE_DRAFT_PATH = _DEFAULT_ARTIFACTS_DIR / "coverage.draft.md"

# Names of the three ship-blocker outputs (replay diffs all of them).
_SHIP_OUTPUTS = (
    "index_tree.json",
    "index_tree.provenance.json",
    "index_tree_evidence.json",
)


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
                f"TZ=UTC LC_ALL=C.UTF-8 python -m book_indexer.assembly ..."
            )


def _input_preflight(
    pdf_path: Path,
    corpus_path: Path,
    concepts_dir: Path,
    tables_dir: Path,
    acronyms_path: Path,
) -> None:
    """Validate that all required inputs exist + acronym fixture is signed off.

    Raises ``RuntimeError`` on any missing input or PENDING_AUTHOR sentinel.
    """
    if not pdf_path.exists():
        raise RuntimeError(f"PDF not found: {pdf_path}")
    if not corpus_path.exists():
        raise RuntimeError(f"corpus not found: {corpus_path}")
    if not concepts_dir.is_dir():
        raise RuntimeError(f"concepts directory missing: {concepts_dir}")
    if not any(concepts_dir.glob("*.json")):
        raise RuntimeError(f"no concepts artifacts in: {concepts_dir}")
    if not tables_dir.is_dir():
        raise RuntimeError(f"tables directory missing: {tables_dir}")
    if not acronyms_path.exists():
        raise RuntimeError(f"acronym overrides not found: {acronyms_path}")
    # Author sign-off gate (D-06 / Wave 0): metadata.curated_by must be a
    # real author, not the PENDING_AUTHOR sentinel.
    data = yaml.safe_load(acronyms_path.read_text(encoding="utf-8")) or {}
    curated_by = (data.get("metadata") or {}).get("curated_by", "")
    if curated_by == "PENDING_AUTHOR":
        raise RuntimeError(
            "acronym_overrides.yaml has metadata.curated_by=='PENDING_AUTHOR' "
            "— author sign-off required before cold build (D-06)"
        )


def _tables_sha(tables_dir: Path) -> dict[str, str]:
    """Compute sha256 for the 3 Phase 3b table artifacts (if present).

    Missing files are silently skipped — Phase 4 cold build runs whether
    or not the Phase 3b artifacts are committed yet.
    """
    out: dict[str, str] = {}
    for name in ("cases.json", "statutes.json", "rules.json"):
        p = tables_dir / name
        if p.exists():
            out[name] = _sha256_of(p)
    return out


def _build_pipeline(patterns_path: Path):
    """Load spaCy en_core_web_lg + add EntityRuler from doctrinal_patterns.yaml.

    Mirrors ``concepts/__main__.py:_build_pipeline``.

    B-10 (CONTEXT 06 D-02; Plan 06-02 cross-callsite patch):
    ``attach_phrase_overrides_to_meta`` populates ``nlp.meta`` so the
    ``normalize_for_lemma`` + ``lemma_bucket_key`` short-circuits honor
    curated YAML overrides BEFORE the intra-word-hyphen regex fragments
    ``cross-examination`` into ``cross examination``.
    """
    import spacy

    from book_indexer.concepts.symbolic import build_doctrinal_nlp
    from book_indexer.ingest.tokenizer import attach_phrase_overrides_to_meta

    nlp = spacy.load("en_core_web_lg")
    nlp = build_doctrinal_nlp(nlp)
    attach_phrase_overrides_to_meta(nlp)
    return nlp


def _build(
    pdf_path: Path,
    out_dir: Path,
    concepts_dir: Path = _DEFAULT_CONCEPTS_DIR,
    corpus_path: Path = _DEFAULT_CORPUS_PATH,
    tables_dir: Path = _DEFAULT_TABLES_DIR,
    acronyms_path: Path = _DEFAULT_ACRONYMS,
    patterns_path: Path = _DEFAULT_PATTERNS,
    max_workers: int = 8,
) -> dict:
    """Run the full Phase 4 build; return a telemetry dict.

    Writes 4 files into ``out_dir``: index_tree.json,
    index_tree.provenance.json, index_tree_evidence.json, coverage.draft.md.
    """
    _input_preflight(pdf_path, corpus_path, concepts_dir, tables_dir, acronyms_path)

    nlp = _build_pipeline(patterns_path)
    pdf_sha256 = _sha256_of(pdf_path)
    tables_sha = _tables_sha(tables_dir)

    tree, ledger_rows = build_index_tree(
        concepts_dir=concepts_dir,
        corpus_path=corpus_path,
        nlp=nlp,
        pdf_sha256=pdf_sha256,
        tables_sha=tables_sha,
        max_workers=max_workers,
    )

    # Atomic emits of the 3 ship-blocker artifacts.
    _atomic_write(
        out_dir / "index_tree.json",
        orjson.dumps(tree.model_dump(mode="json"), option=_OPTS),
    )
    _atomic_write(
        out_dir / "index_tree.provenance.json",
        orjson.dumps(tree.provenance.model_dump(mode="json"), option=_OPTS),
    )
    _atomic_write(
        out_dir / "index_tree_evidence.json",
        orjson.dumps({"entries": ledger_rows}, option=_OPTS),
    )

    # Draft coverage report (D-08; Phase 5 finalizes).
    emit_draft_report(
        tree.provenance.model_dump(mode="json"),
        out_dir / "coverage.draft.md",
    )

    prov = tree.provenance
    return {
        "entries": len(tree.entries),
        "evidence_rows": len(ledger_rows),
        "oob_status": prov.oob_status,
        "pre_dedup_count": prov.pre_dedup_count,
        "post_dedup_count": prov.post_dedup_count,
        "post_deconflict_count": prov.post_deconflict_count,
        "post_zero_evidence_count": prov.post_zero_evidence_count,
        "oversize_parent_count": prov.oversize_parent_count,
        "sub_entry_total_count": prov.sub_entry_total_count,
        "slug_collision_count": prov.slug_collision_count,
        "iteration_depth": prov.iteration_depth,
        "dropped_table_citations": len(prov.dropped_table_citations),
        "zero_evidence_drops": len(prov.zero_evidence_drops),
    }


def _replay() -> int:
    """Re-run build into a tmpdir; diff vs committed copy.

    Returns 0 IFF every ship-blocker output is byte-identical; 1
    otherwise (with diff summary on stderr).
    """
    if not all((_DEFAULT_ARTIFACTS_DIR / n).exists() for n in _SHIP_OUTPUTS):
        sys.stderr.write(
            f"committed artifacts not found at {_DEFAULT_ARTIFACTS_DIR}; "
            "run `build` first\n"
        )
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        _build(_DEFAULT_PDF, tmp_dir)
        mismatches: list[str] = []
        for fname in _SHIP_OUTPUTS:
            committed = _DEFAULT_ARTIFACTS_DIR / fname
            regenerated = tmp_dir / fname
            if not committed.exists():
                mismatches.append(f"missing committed: {fname}")
                continue
            if not regenerated.exists():
                mismatches.append(f"missing regenerated: {fname}")
                continue
            if not filecmp.cmp(committed, regenerated, shallow=False):
                mismatches.append(f"byte-mismatch: {fname}")
        if mismatches:
            sys.stderr.write(
                "REPLAY MISMATCHES:\n  " + "\n  ".join(mismatches) + "\n"
            )
            return 1
        sys.stdout.write(
            f"replay OK: {len(_SHIP_OUTPUTS)} artifacts byte-identical\n"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="book_indexer.assembly",
        description="Phase 4 canonicalization & index-assembly pipeline (build / replay).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="Compose pipeline → 3 IndexTree artifacts")
    p_build.add_argument("pdf_path", nargs="?", default=str(_DEFAULT_PDF))
    sub.add_parser("replay", help="Re-build into tmpdir; diff vs committed (Lock #5)")
    args = parser.parse_args(argv)

    try:
        _env_preflight()
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    if args.cmd == "build":
        t0 = time.monotonic()
        try:
            telemetry = _build(Path(args.pdf_path), _DEFAULT_ARTIFACTS_DIR)
        except RuntimeError as exc:
            sys.stderr.write(f"ERROR: {exc}\n")
            return 1
        telemetry["wall_clock_s"] = round(time.monotonic() - t0, 3)
        sys.stdout.write(orjson.dumps(telemetry, option=_OPTS).decode("utf-8") + "\n")
        return 0
    if args.cmd == "replay":
        return _replay()
    return 2  # unreachable


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
