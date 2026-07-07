"""``index-book verify-against OLD_INDEX_TREE PDF`` — D-04 entry-level IR diff.

Per CONTEXT 06 D-04:

1. Parse OLD_INDEX_TREE (read its bytes via orjson). Parse error → exit 3.
2. Run the assembly sub-pipeline against committed inputs to produce a fresh
   ``artifacts/index_tree.json``.
3. Compute entry-level diff via :func:`differ.diff_index_trees`.
4. Emit JSON diff to stdout (sorted keys for determinism).
5. Apply ``--allow-drift N`` policy: drift = ``len(removed) + len(changed)``
   (added entries are NOT counted toward drift; only contraction or content
   change blocks). If ``drift > allow_drift`` → exit 1.

Exit codes:

* 0 — diff within ``--allow-drift`` allowance
* 1 — drift exceeds allowance (CI ship-blocker)
* 2 — sub-pipeline failure (assembly exit non-zero)
* 3 — parse error on OLD_INDEX_TREE
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import orjson

from ..differ import diff_index_trees

REPO_ROOT = Path(__file__).resolve().parents[4]
INDEX_TREE_PATH = REPO_ROOT / "artifacts" / "index_tree.json"


def run(
    old_index_tree: Path,
    pdf: Path,
    allow_drift: int = 0,
) -> int:
    # Determinism env preflight (RESEARCH §H-13 Pitfall 6)
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("TZ", "UTC")
    os.environ.setdefault("LC_ALL", "C.UTF-8")
    env = {**os.environ}

    # 1. Parse OLD_INDEX_TREE
    try:
        old_ir = orjson.loads(old_index_tree.read_bytes())
    except Exception as exc:
        sys.stderr.write(
            f"ERROR: failed to parse old IR at {old_index_tree}: {exc}\n"
        )
        return 3

    # 2. Build NEW IR via assembly sub-pipeline.
    # Capture assembly's stdout (telemetry JSON) so it does NOT pollute the
    # diff JSON we emit on stdout. Per CONTEXT D-04 the verify-against
    # subcommand's stdout contract is "single JSON object {added, removed,
    # changed}" — assembly's build telemetry is forwarded to stderr instead.
    cmd = ["uv", "run", "python", "-m", "book_indexer.assembly", "build"]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(
            f"ERROR: assembly build exit {proc.returncode}\n"
        )
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        # Map to exit 2 (env / sub-pipeline failure) per CONTEXT D-04.
        return 2
    # Forward assembly telemetry to stderr so users still see it.
    if proc.stdout:
        sys.stderr.write("assembly telemetry: " + proc.stdout.rstrip() + "\n")

    # 3. Read fresh index_tree.json.
    try:
        new_ir = orjson.loads(INDEX_TREE_PATH.read_bytes())
    except Exception as exc:
        sys.stderr.write(
            f"ERROR: failed to read fresh {INDEX_TREE_PATH}: {exc}\n"
        )
        return 2

    # 4. Compute entry-level diff.
    diff = diff_index_trees(old_ir, new_ir)

    # 5. Emit JSON diff to stdout (default=str for any Path-like leftovers).
    sys.stdout.write(
        json.dumps(diff, indent=2, sort_keys=True, default=str) + "\n"
    )

    # 6. Apply --allow-drift policy.
    drift_count = len(diff["removed"]) + len(diff["changed"])
    sys.stderr.write(
        f"Verify-against: {len(diff['added'])} added, "
        f"{len(diff['removed'])} removed, "
        f"{len(diff['changed'])} changed "
        f"(drift={drift_count}, allowed={allow_drift}).\n"
    )
    if drift_count > allow_drift:
        return 1
    return 0
