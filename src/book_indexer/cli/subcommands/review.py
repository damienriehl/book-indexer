"""``index-book review --sample N`` — QUAL-02 deterministic stratified sample.

Per CONTEXT 06 D-06 + RESEARCH §H-7:

* Read ``artifacts/index_tree.json`` + ``artifacts/index_tree_evidence.json``.
* Filter to multi-locator entries (``len(locators) >= 2``) — stratification
  yields higher-signal review surface (D-06).
* Deterministic sample: ``random.Random(0).sample(...)``; with PYTHONHASHSEED=0
  the result is byte-identical across runs.
* Sort by canonical for stable rendering order.
* Emit ``artifacts/audit/sample_review.md`` — checkbox markdown listing the
  first 3 locators per entry with ≤120-char ``verbatim_snippet``.
* First-run path: if ``index_tree.json`` doesn't exist, emit a placeholder
  pointing the user to ``index-book build PDF`` first (D-06 first-run policy).

The ``[project.scripts]`` entry-point app provides a typer ``--sample N``
flag (default 20) that calls into this ``run(sample=N)`` function.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import orjson

REPO_ROOT = Path(__file__).resolve().parents[4]
INDEX_TREE_PATH = REPO_ROOT / "artifacts" / "index_tree.json"
EVIDENCE_PATH = REPO_ROOT / "artifacts" / "index_tree_evidence.json"
SAMPLE_PATH = REPO_ROOT / "artifacts" / "audit" / "sample_review.md"


def run(sample: int = 20) -> int:
    # First-run path (no IR yet): emit placeholder + return 0.
    if not INDEX_TREE_PATH.exists():
        SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SAMPLE_PATH.write_bytes(
            b"# Sample Review (no IR yet \xe2\x80\x94 run "
            b"`index-book build PDF` first)\n"
        )
        return 0

    tree = orjson.loads(INDEX_TREE_PATH.read_bytes())
    if EVIDENCE_PATH.exists():
        evidence_data = orjson.loads(EVIDENCE_PATH.read_bytes())
    else:
        evidence_data = {"entries": []}

    # The evidence file's exact shape is Phase 4-locked. Per RESEARCH §H-7
    # it's `{"entries": [...]}` with each row keyed by integer `id` →
    # the same ID stored on each Locator as `evidence_id`.
    evidence_rows = evidence_data.get(
        "entries", evidence_data.get("evidence", [])
    )
    evidence_by_id: dict[object, dict] = {}
    for row in evidence_rows:
        ev_id = row.get("id", row.get("evidence_id"))
        evidence_by_id[ev_id] = row

    multi_locator = [
        e for e in tree.get("entries", [])
        if len(e.get("locators", [])) >= 2
    ]

    # Deterministic sample: PYTHONHASHSEED=0 + Random(0) → byte-identical.
    rng = random.Random(0)
    n = min(sample, len(multi_locator))
    selected = rng.sample(multi_locator, n)
    selected.sort(key=lambda e: e.get("canonical", ""))  # stable rendering

    lines: list[str] = [
        f"# Sample Review (N={sample}, seed=0, stratified ≥2-locator)",
        "",
        f"_{n} of {len(multi_locator)} multi-locator entries; "
        f"reviewer ticks each box._",
        "",
    ]
    for entry in selected:
        canonical = entry.get("canonical", "")
        for locator in entry.get("locators", [])[:3]:
            ev = evidence_by_id.get(locator.get("evidence_id"))
            snippet = (ev or {}).get("verbatim_snippet", "(no evidence found)")
            snippet = snippet[:120]
            lines.append(
                f"- [ ] **{canonical}** § {locator.get('section_ref', '?')} "
                f"(p. {locator.get('folio', '?')}) — {snippet!r}"
            )
        lines.append("")

    SAMPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_PATH.write_bytes("\n".join(lines).encode("utf-8"))
    sys.stdout.write(
        f"Sample review emitted: {SAMPLE_PATH.relative_to(REPO_ROOT)}\n"
    )
    return 0
