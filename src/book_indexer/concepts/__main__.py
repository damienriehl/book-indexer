"""Phase 3a v2: CLI entrypoint for pure-symbolic concept discovery.

Subcommands:

* ``build``  — run all 3 symbolic passes × 5 chapters; write 30 D-28
               artifacts (15 ``{pass_type}_ch{N}.json`` + 15
               ``{pass_type}_ch{N}.provenance.json``) to
               ``artifacts/concepts/`` (default).
* ``replay`` — regenerate fresh artifacts to a tmpdir; byte-compare
               against the committed copy. Exits 0 on byte-identical
               match (Lock #5 verification), 1 on any drift.

The v1 ``auth-check`` subcommand is intentionally NOT registered (per
D-32: no LLM auth needed in v2). ``argparse`` rejects unknown subcommands
with exit code 2 — see ``test_unknown_subcommand_exits_2``.

Determinism preflight:
The CLI VALIDATES the deterministic env vars are set; it does NOT set
them itself (matches the Phase 1/2 CLI contract).

requirements_addressed: CON-04, CON-06
"""
from __future__ import annotations

import argparse
import filecmp
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import orjson
import spacy

from .chunker import open_read_only_corpus
from .passes import CallResult, run_all_symbolic
from .symbolic import build_doctrinal_nlp

__all__ = [
    "RunSummary",
    "main",
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CORPUS = _REPO_ROOT / "artifacts" / "page_corpus.sqlite"
DEFAULT_OUTPUT_DIR = _REPO_ROOT / "artifacts" / "concepts"
DEFAULT_PATTERNS = _REPO_ROOT / "fixtures" / "doctrinal_patterns.yaml"


# ---------------------------------------------------------------------------
# RunSummary — telemetry shape replacing v1 LLM-call metrics
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    """Per-run telemetry summarizing one symbolic build.

    Replaces v1's LLM-centric shape (subprocess_invocations / cache_hits /
    failure_breakdown) with metrics that fit the symbolic pipeline:
    wall-clock + per-pass candidate counts + per-chunk candidate counts +
    artifact/provenance write counts + a list of ``(chunk_id, pass_type,
    error)`` failure tuples.

    Mutable by design — ``cmd_build`` populates fields incrementally,
    then serializes via ``to_dict`` for byte-deterministic stdout
    emission.
    """

    wall_clock_s: float = 0.0
    per_pass_counts: dict[str, int] = field(default_factory=dict)
    per_chunk_counts: dict[str, int] = field(default_factory=dict)
    artifacts_written: int = 0
    provenance_written: int = 0
    failures: list[tuple[str, str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "wall_clock_s": round(self.wall_clock_s, 3),
            "per_pass_counts": dict(sorted(self.per_pass_counts.items())),
            "per_chunk_counts": dict(sorted(self.per_chunk_counts.items())),
            "artifacts_written": self.artifacts_written,
            "provenance_written": self.provenance_written,
            "failures": list(self.failures),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _env_preflight() -> None:
    """Determinism preflight (Lock #5 / Phase 1 D-20).

    Asserts ``PYTHONHASHSEED=0``, ``TZ=UTC``, ``LC_ALL=C.UTF-8``.
    Raises ``RuntimeError`` on mismatch (caught by ``main()`` → exit 1).
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
                f"TZ=UTC LC_ALL=C.UTF-8 python -m book_indexer.concepts ..."
            )


def _build_pipeline(patterns_path: Path) -> spacy.language.Language:
    """Load ``en_core_web_lg`` + add EntityRuler from
    ``doctrinal_patterns.yaml`` (idempotent).

    B-10 (CONTEXT 06 D-02; Plan 06-02 cross-callsite patch):
    ``attach_phrase_overrides_to_meta`` populates ``nlp.meta`` so the
    ``canonical_form_key`` short-circuit honors the curated YAML overrides
    and emits ``cross-examination`` (single canonical) instead of
    ``cross - examination`` (re-fragmented by the default tokenizer).
    """
    from book_indexer.ingest.tokenizer import attach_phrase_overrides_to_meta

    nlp = spacy.load("en_core_web_lg")
    nlp = build_doctrinal_nlp(nlp)
    attach_phrase_overrides_to_meta(nlp)
    return nlp


def _summarize(results: list[CallResult]) -> RunSummary:
    """Aggregate per-pass + per-chunk candidate counts from CallResults."""
    summary = RunSummary()
    for r in results:
        if r.error is not None:
            summary.failures.append((r.chunk_id, r.pass_type, r.error))
            continue
        count = len(r.response.candidates) if r.response else 0
        summary.per_pass_counts[r.pass_type] = (
            summary.per_pass_counts.get(r.pass_type, 0) + count
        )
        summary.per_chunk_counts[r.chunk_id] = (
            summary.per_chunk_counts.get(r.chunk_id, 0) + count
        )
    success = [r for r in results if r.error is None]
    summary.artifacts_written = len(success)
    summary.provenance_written = len(success)
    return summary


def _emit_telemetry(summary: RunSummary) -> None:
    """Print one byte-deterministic JSON document on stdout (sorted keys + indent-2)."""
    out = orjson.dumps(
        summary.to_dict(),
        option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2,
    )
    sys.stdout.write(out.decode("utf-8") + "\n")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    """Cold-build entry point: writes the 30 D-28 artifacts.

    Exit codes:
        0 — all (pass × chapter) succeeded; 30 files written.
        1 — corpus missing, patterns missing, or one or more passes raised.
    """
    try:
        _env_preflight()
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    if not args.corpus.exists():
        sys.stderr.write(f"corpus not found: {args.corpus}\n")
        return 1
    if not args.patterns.exists():
        sys.stderr.write(f"doctrinal patterns not found: {args.patterns}\n")
        return 1
    args.output_dir.mkdir(parents=True, exist_ok=True)
    nlp = _build_pipeline(args.patterns)
    conn = open_read_only_corpus(args.corpus)
    chapters = tuple(args.chapters) if args.chapters else (1, 2, 3, 4, 5)
    t0 = time.perf_counter()
    try:
        results = run_all_symbolic(
            conn,
            nlp,
            args.output_dir,
            chapters=chapters,
            corpus_path=args.corpus,
            doctrinal_patterns_path=args.patterns,
        )
    finally:
        conn.close()
    wall = time.perf_counter() - t0
    summary = _summarize(results)
    summary.wall_clock_s = wall
    _emit_telemetry(summary)
    return 0 if not summary.failures else 1


def cmd_replay(args: argparse.Namespace) -> int:
    """Regenerate fresh artifacts; byte-compare against committed copy.

    This is the Lock #5 verification — every file in
    ``args.output_dir/*.json`` must be byte-identical to a fresh build into
    a tmpdir. Drift causes exit code 1 with a per-file diff list on stderr.
    """
    try:
        _env_preflight()
    except RuntimeError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    if not args.output_dir.exists():
        sys.stderr.write(
            f"committed artifacts not found at {args.output_dir}; "
            "run `build` first\n"
        )
        return 1
    if not args.corpus.exists():
        sys.stderr.write(f"corpus not found: {args.corpus}\n")
        return 1
    if not args.patterns.exists():
        sys.stderr.write(f"doctrinal patterns not found: {args.patterns}\n")
        return 1
    chapters = tuple(args.chapters) if args.chapters else (1, 2, 3, 4, 5)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_out = Path(tmpdir)
        nlp = _build_pipeline(args.patterns)
        conn = open_read_only_corpus(args.corpus)
        try:
            run_all_symbolic(
                conn,
                nlp,
                tmp_out,
                chapters=chapters,
                corpus_path=args.corpus,
                doctrinal_patterns_path=args.patterns,
            )
        finally:
            conn.close()
        # Byte-compare every committed file against fresh.
        mismatches: list[str] = []
        committed_files = sorted(args.output_dir.glob("*.json"))
        for committed in committed_files:
            fresh = tmp_out / committed.name
            if not fresh.exists():
                mismatches.append(f"missing fresh: {committed.name}")
                continue
            if not filecmp.cmp(committed, fresh, shallow=False):
                mismatches.append(f"byte-mismatch: {committed.name}")
        if mismatches:
            sys.stderr.write(
                "REPLAY MISMATCHES:\n  " + "\n  ".join(mismatches) + "\n"
            )
            return 1
        sys.stdout.write(
            f"replay OK: {len(committed_files)} artifacts byte-identical\n"
        )
        return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="book_indexer.concepts",
        description="Phase 3a v2 pure-symbolic concept discovery.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_build = sub.add_parser(
        "build",
        help="Run symbolic passes; write artifacts/concepts/*.json",
    )
    p_replay = sub.add_parser(
        "replay",
        help="Regenerate fresh; byte-compare against committed (Lock #5)",
    )
    # NOTE: ``auth-check`` is intentionally NOT registered (D-32 — dropped).

    for p in (p_build, p_replay):
        p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
        p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
        p.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
        p.add_argument(
            "--chapters",
            type=lambda s: [int(x) for x in s.split(",")],
            default=None,
            help="comma-separated chapter list (default: 1,2,3,4,5)",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "replay":
        return cmd_replay(args)
    parser.error(f"unknown command: {args.cmd}")
    return 2  # unreachable — argparse.error raises SystemExit(2).


if __name__ == "__main__":
    raise SystemExit(main())
