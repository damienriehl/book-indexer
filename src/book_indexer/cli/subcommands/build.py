"""``index-book build PDF`` (default subcommand).

Wires together Phases 1-5 sub-pipelines based on ``--rebuild-*`` flags
(CONTEXT 06 D-03 + D-05 + RESEARCH §H-1 / Open-Q1 / §H-13 Pitfall 6):

1. **Determinism env preflight** — ``os.environ.setdefault`` for
   ``PYTHONHASHSEED=0``, ``TZ=UTC``, ``LC_ALL=C.UTF-8`` BEFORE invoking
   sub-pipelines (RESEARCH §H-13 Pitfall 6 — without this, byte-identity
   drifts under different shell envs).
2. **CLI-03 PDF SHA-256 preflight** — fail-fast contract; mismatch → exit 2.
3. **Delegated paths** — ``--verify-against`` defers to verify subcommand;
   ``--sample-review`` defers to review subcommand.
4. **Rebuild paths** — ``--rebuild-{concepts,tables,index-tree,all}`` shell
   ``subprocess.run([uv, run, python, -m, book_indexer.<stage>, build])``
   sequentially with the explicit determinism env dict. ``--rebuild-index-tree``
   targets the ``assembly`` sub-pipeline (NOT a module named ``index_tree``)
   per CONTEXT 06 D-05.
5. **Default render path** — direct in-process call to
   ``book_indexer.render.__main__.main(["build"])`` per RESEARCH Open-Q1
   for the typical ~3s render-only re-emit.

Exit codes (CONTEXT 06 D-04):

* 0 — success
* 1 — sub-pipeline drift / ship-blocker
* 2 — PDF SHA-256 mismatch (CLI-03)
* 3 — verify-against parse error (delegated to verify.run)

Plan 06-01 implementation. Plan 06-04 will author the smoke + byte-identity
tests against this entry point.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..differ import (
    check_pdf_matches_committed_artifacts,
    diagnose_mismatch,
)


def _set_determinism_env() -> dict[str, str]:
    """Set + return the determinism env dict (RESEARCH §H-13 Pitfall 6).

    ``os.environ.setdefault`` is intentional: if the user has already exported
    these vars (the canonical CI-shell preamble), we don't clobber. The
    returned dict is a snapshot for explicit ``subprocess.run(env=...)`` —
    relying on inheritance alone is fragile (Pitfall 6).
    """
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("TZ", "UTC")
    os.environ.setdefault("LC_ALL", "C.UTF-8")
    return {**os.environ}


def _run_subpipeline(module: str, env: dict[str, str], extra_args: list[str] | None = None) -> int:
    """``uv run python -m book_indexer.<module> build [extra_args]``.

    ``extra_args`` for ingest's positional PDF path; defaults to ``["build"]``
    for the symmetric subcommand-style sub-pipelines.
    """
    cmd = ["uv", "run", "python", "-m", f"book_indexer.{module}"]
    if extra_args is None:
        cmd.append("build")
    else:
        cmd.extend(extra_args)
    proc = subprocess.run(cmd, env=env)
    return proc.returncode


def run(
    pdf: Path,
    rebuild_all: bool = False,
    rebuild_concepts: bool = False,
    rebuild_tables: bool = False,
    rebuild_index_tree: bool = False,
    sample_review: int = 0,
    verify_against: Path | None = None,
    allow_drift: int = 0,
) -> int:
    # 1. Determinism env preflight (RESEARCH §H-13 Pitfall 6)
    env = _set_determinism_env()

    # 2. CLI-03 PDF SHA-256 preflight (fail-fast contract)
    is_match, committed_sha = check_pdf_matches_committed_artifacts(pdf)
    if not is_match and committed_sha is not None:
        sys.stderr.write(diagnose_mismatch(pdf, committed_sha))
        return 2

    # 3. Delegated paths (verify-against / sample-review)
    if verify_against is not None:
        from . import verify as verify_mod
        return verify_mod.run(
            old_index_tree=verify_against, pdf=pdf, allow_drift=allow_drift
        )
    if sample_review > 0:
        from . import review as review_mod
        return review_mod.run(sample=sample_review)

    # 4. Rebuild paths (CONTEXT 06 D-05)
    rebuilds_required: list[str] = []
    if rebuild_all:
        # ingest takes the PDF as a positional arg; concepts/tables/assembly
        # all use the build subcommand and read committed inputs.
        rebuilds_required = ["ingest", "concepts", "tables", "assembly"]
    else:
        if rebuild_concepts:
            rebuilds_required.append("concepts")
        if rebuild_tables:
            rebuilds_required.append("tables")
        if rebuild_index_tree:
            # NOTE: --rebuild-index-tree maps to the `assembly` module
            # (per CONTEXT 06 D-05).
            rebuilds_required.append("assembly")
    for stage in rebuilds_required:
        if stage == "ingest":
            # ingest takes the PDF as a positional argument (no subcommand).
            cmd = ["uv", "run", "python", "-m", "book_indexer.ingest", str(pdf)]
            proc = subprocess.run(cmd, env=env)
            if proc.returncode != 0:
                sys.stderr.write(
                    f"ERROR: book_indexer.ingest exit {proc.returncode}\n"
                )
                return proc.returncode
        else:
            rc = _run_subpipeline(stage, env)
            if rc != 0:
                sys.stderr.write(f"ERROR: book_indexer.{stage} exit {rc}\n")
                return rc

    # 5. Default render path — direct in-process call to render.__main__.main
    # (RESEARCH Open-Q1; ~3s typical wall-clock).
    #
    # NOTE: render's __main__ module (Phase 5) exposes `main(argv)` rather
    # than the `cmd_build(args)` shape used by concepts/. Calling
    # render.main(["build"]) preserves the determinism preflight that
    # render.main runs internally (PYTHONHASHSEED/TZ/LC_ALL check) and
    # propagates its exit code cleanly.
    from book_indexer.render.__main__ import main as render_main
    return render_main(["build"])
