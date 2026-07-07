"""``index-book replay`` — QUAL-01 byte-identity sweep across 4 sub-pipelines.

Per RESEARCH §H-9 cascade order: invoke ``concepts`` → ``tables`` → ``assembly``
→ ``render`` (in this order; downstream depends on upstream artifacts) via
``subprocess.run([uv, run, python, -m, book_indexer.<sub>, replay])`` with
the explicit determinism env dict. Aggregate failures (collect names of all
sub-pipelines that drifted; exit 1 if any).

ingest's ``__main__`` does NOT expose a ``replay`` subcommand (Phase 1 chose
to surface byte-identity verification through downstream replays). Per the
plan's read-the-live-state guidance, we omit ingest from the cascade.

Determinism env (RESEARCH §H-13 Pitfall 6) is propagated explicitly to each
subprocess; ``shell=True`` is NEVER used.

Exit codes:

* 0 — all 4 sub-pipelines byte-identical (QUAL-01 GREEN)
* 1 — one or more drifted; failures listed on stderr
"""
from __future__ import annotations

import os
import subprocess
import sys

# Cascade order per RESEARCH §H-9 (downstream-after-upstream).
SUB_PIPELINES = ("concepts", "tables", "assembly", "render")


def run() -> int:
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("TZ", "UTC")
    os.environ.setdefault("LC_ALL", "C.UTF-8")
    env = {**os.environ}

    failures: list[str] = []
    for module in SUB_PIPELINES:
        cmd = [
            "uv",
            "run",
            "python",
            "-m",
            f"book_indexer.{module}",
            "replay",
        ]
        proc = subprocess.run(cmd, env=env)
        if proc.returncode != 0:
            failures.append(module)

    if failures:
        sys.stderr.write(
            f"QUAL-01 replay FAILED — {len(failures)} sub-pipeline(s) drifted: "
            f"{', '.join(failures)}\n"
        )
        return 1
    sys.stdout.write(
        "QUAL-01 replay OK: all 4 sub-pipelines byte-identical\n"
    )
    return 0
