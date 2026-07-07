"""CLI entry — ``python -m book_indexer.verify build-ledger [--corpus ...] [--out ...]``.

Regenerates ``artifacts/index_evidence.json`` from the live the source book corpus.
Reads the curated phrase vocabulary from ``fixtures/legal_phrase_vocab.yaml``;
loops every term through ``verify()`` and writes the sorted, indent-2 JSON
ledger via :func:`book_indexer.verify.ledger.build_ledger`.

Deterministic-environment note: this CLI assumes ``PYTHONHASHSEED=0``,
``TZ=UTC``, ``LC_ALL=C.UTF-8`` are set by the caller (shell / CI / test
preflight). The program itself does not set them — caller responsibility
mirrors ``book_indexer.ingest``'s CLI contract.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import yaml

from .ledger import build_ledger

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_CORPUS = _REPO_ROOT / "artifacts" / "page_corpus.sqlite"
_DEFAULT_OUT = _REPO_ROOT / "artifacts" / "index_evidence.json"
_VOCAB_PATH = _REPO_ROOT / "fixtures" / "legal_phrase_vocab.yaml"


def _load_terms(vocab_path: Path = _VOCAB_PATH) -> list[str]:
    """Load the phrase list from fixtures/legal_phrase_vocab.yaml."""
    with vocab_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [row["term"] for row in data["phrases"]]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m book_indexer.verify")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-ledger", help="Write artifacts/index_evidence.json")
    b.add_argument("--corpus", type=Path, default=_DEFAULT_CORPUS,
                   help=f"Path to page_corpus.sqlite (default: {_DEFAULT_CORPUS})")
    b.add_argument("--out", type=Path, default=_DEFAULT_OUT,
                   help=f"Output path (default: {_DEFAULT_OUT})")
    b.add_argument("--vocab", type=Path, default=_VOCAB_PATH,
                   help=f"Phrase vocab YAML (default: {_VOCAB_PATH})")

    args = p.parse_args(argv)

    if args.cmd == "build-ledger":
        terms = _load_terms(args.vocab)
        conn = sqlite3.connect(str(args.corpus))
        conn.execute("PRAGMA query_only = 1")
        try:
            n = build_ledger(conn, terms, args.out)
        finally:
            conn.close()
        print(f"wrote {args.out} ({n} Evidence rows)")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
