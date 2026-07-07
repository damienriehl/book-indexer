"""CLI entry: ``python -m book_indexer.ingest <pdf> [--out-dir PATH]``.

Minimal argparse wrapper around :func:`book_indexer.ingest.pipeline.run_ingest`.
The final ``index-book`` CLI lives in Phase 6; this is the dev / test entry
point that Plan 01-05's determinism gate drives via ``subprocess.run``.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the ingest pipeline; returns an exit code."""
    # Belt-and-suspenders determinism environment. If the caller has these set
    # we respect them; if not, we default to the canonical frozen values.
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("TZ", "UTC")

    # Late import — keeps the --help path fast and avoids pulling spaCy into
    # the process until we actually need to tokenize.
    from book_indexer.ingest.pipeline import run_ingest

    parser = argparse.ArgumentParser(
        prog="book-index-ingest",
        description="Phase-1 deterministic ingest: PDF -> page_corpus.sqlite",
    )
    parser.add_argument("pdf", type=Path, help="Path to the input PDF")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("artifacts"),
        help="Output directory (default: artifacts/)",
    )
    args = parser.parse_args(argv)

    if not args.pdf.is_file():
        print(f"error: PDF not found: {args.pdf}", file=sys.stderr)
        return 2

    out = run_ingest(args.pdf, args.out_dir)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
