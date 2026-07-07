"""PDF SHA-256 + index-tree diff helpers (CLI-03 + D-04) + QUAL-02 acceptance gate.

* :func:`compute_pdf_sha256` — chunked SHA-256 of an input PDF (<50ms on a
  ~2 MB file per RESEARCH §H-6).
* :func:`check_pdf_matches_committed_artifacts` — compares the input PDF's
  hash to ``artifacts/audit/metadata.json:pdf_sha256``. Silent pass on
  first-run / missing pdf_sha256 field. Never raises.
* :func:`diagnose_mismatch` — user-facing CLI-03 diagnostic message.
* :func:`diff_index_trees` — entry-level diff between two IndexTree IRs.
  Compare-key: ``canonical``. Locator compare-key: ``(section_ref, folio)``
  — drops ``evidence_id`` (internal renumbering is not a content change
  per CONTEXT 06 D-04).
* :func:`check_sample_review_acceptance` — QUAL-02 ship-blocker acceptance
  gate. Reads ``artifacts/audit/sample_review_acceptance.json`` (if present)
  and FAILs (returns 1) when ``acceptance_rate`` falls below the 0.90
  threshold (CONTEXT 06 D-06). First-run silent pass when the file is
  absent (Plan 06-05 user-checkpoint creates it).

Plan 06-01 implementation; Plan 06-04 appended ``check_sample_review_acceptance``.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[3]
METADATA_PATH = REPO_ROOT / "artifacts" / "audit" / "metadata.json"


def compute_pdf_sha256(pdf_path: Path) -> str:
    """Compute the SHA-256 hex digest of ``pdf_path`` via 65 KB chunked reads.

    <50ms on a ~2 MB PDF per RESEARCH §H-6. Caller is responsible for
    ensuring ``pdf_path`` exists; typer's ``exists=True`` argument validator
    handles this at the CLI boundary.
    """
    h = hashlib.sha256()
    with pdf_path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_pdf_matches_committed_artifacts(
    pdf_path: Path,
) -> tuple[bool, str | None]:
    """Compare ``pdf_path`` SHA-256 against committed ``metadata.json:pdf_sha256``.

    Returns:
        (True, None) — first-run (no metadata.json) OR no ``pdf_sha256``
            field present (silent pass; nothing to compare against).
        (True, committed_sha) — input matches committed artifacts.
        (False, committed_sha) — mismatch; caller emits diagnostic via
            :func:`diagnose_mismatch` and exits 2.
    """
    if not METADATA_PATH.exists():
        return (True, None)
    metadata = orjson.loads(METADATA_PATH.read_bytes())
    committed_sha = metadata.get("pdf_sha256")
    if not committed_sha:
        return (True, None)
    actual_sha = compute_pdf_sha256(pdf_path)
    return (actual_sha == committed_sha, committed_sha)


def diagnose_mismatch(pdf_path: Path, committed_sha: str) -> str:
    """Build the user-facing CLI-03 diagnostic message (RESEARCH §H-6)."""
    actual = compute_pdf_sha256(pdf_path)
    try:
        meta_rel = METADATA_PATH.relative_to(REPO_ROOT)
    except ValueError:
        meta_rel = METADATA_PATH
    return (
        f"ERROR: input PDF SHA-256 mismatch\n"
        f"  input PDF      : {pdf_path}\n"
        f"  input SHA      : {actual}\n"
        f"  committed SHA  : {committed_sha}\n"
        f"  committed in   : {meta_rel}\n"
        f"  This means the committed artifacts under artifacts/{{render,audit,...}}\n"
        f"  were built against a DIFFERENT PDF. To rebuild against the new PDF, run\n"
        f"  with --rebuild-all. To check what changed, run with --verify-against\n"
        f"  artifacts/index_tree.json.\n"
    )


def diff_index_trees(
    old_ir: dict[str, Any],
    new_ir: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Entry-level diff between two IndexTree IRs (CONTEXT 06 D-04).

    Compare-key: ``canonical`` (the entry-level identifier). Returns::

        {
          "added":   [new entries not in old],
          "removed": [old entries not in new],
          "changed": [{"canonical", "old", "new", "fields_changed"}, ...],
        }

    Locator compare granularity drops ``evidence_id`` (internal renumbering is
    NOT a content change per D-04). Variants are compared as-is. Other fields
    (sub_entries, see, see_also, sort_key, derived_from_table) are ignored at
    this layer — entry-level diff cares about CONTENT (locators + variants),
    not internal bookkeeping.
    """
    old_by_canonical = {e["canonical"]: e for e in old_ir.get("entries", [])}
    new_by_canonical = {e["canonical"]: e for e in new_ir.get("entries", [])}

    added = [e for c, e in new_by_canonical.items() if c not in old_by_canonical]
    removed = [e for c, e in old_by_canonical.items() if c not in new_by_canonical]
    changed: list[dict[str, Any]] = []
    for c, new_e in new_by_canonical.items():
        if c not in old_by_canonical:
            continue
        old_e = old_by_canonical[c]
        old_locs = sorted(
            (loc.get("section_ref", ""), loc.get("folio", ""))
            for loc in old_e.get("locators", [])
        )
        new_locs = sorted(
            (loc.get("section_ref", ""), loc.get("folio", ""))
            for loc in new_e.get("locators", [])
        )
        fields_changed: list[str] = []
        if old_locs != new_locs:
            fields_changed.append("locators")
        if old_e.get("variants", []) != new_e.get("variants", []):
            fields_changed.append("variants")
        if fields_changed:
            changed.append(
                {
                    "canonical": c,
                    "old": old_e,
                    "new": new_e,
                    "fields_changed": fields_changed,
                }
            )

    return {"added": added, "removed": removed, "changed": changed}


# ============================================================================
# QUAL-02: sample-review acceptance gate (CONTEXT 06 D-06; RESEARCH §H-7)
# ============================================================================


class RejectedEntry(BaseModel):
    """A single entry the human reviewer rejected during sample review (QUAL-02).

    Carries the canonical/section/folio triple that was rejected plus a
    short reason string. The schema is ``frozen`` + ``extra="forbid"`` —
    Lock #2 mirror (no schema drift, no silent additional fields).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    canonical: str
    section_ref: str
    folio: str
    reason: str


class SampleReviewAcceptance(BaseModel):
    """QUAL-02 acceptance file — committed by the Plan 06-05 user-checkpoint.

    Recorded once per release after the user reviews
    ``artifacts/audit/sample_review.md``. ``acceptance_rate`` MUST equal
    ``accepted / sample_size`` and is the ship-blocker threshold input
    (CONTEXT 06 D-06: ≥ 0.90 to ship; below → FAIL).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    sample_size: int
    accepted: int
    rejected: int
    acceptance_rate: float = Field(ge=0.0, le=1.0)
    reviewed_at: str  # ISO-8601
    reviewer: str
    rejected_entries: list[RejectedEntry] = []


def check_sample_review_acceptance() -> int:
    """QUAL-02 ship-blocker per CONTEXT 06 D-06.

    Reads ``artifacts/audit/sample_review_acceptance.json``. The file is
    produced by the Plan 06-05 user-checkpoint after the human reviewer
    walks the sample-review document.

    Returns:
        0 — file missing (WARN; first-run silent pass) OR
            ``acceptance_rate >= 0.90``.
        1 — ``acceptance_rate < 0.90`` (FAIL ship-blocker).

    Raises:
        ``pydantic.ValidationError`` — malformed JSON / schema drift.
    """
    p = REPO_ROOT / "artifacts" / "audit" / "sample_review_acceptance.json"
    if not p.exists():
        sys.stderr.write(
            "WARN: QUAL-02 sample_review_acceptance.json not found; "
            "first-run skip — run `index-book review` and supply the "
            "acceptance file before release.\n"
        )
        return 0
    data = SampleReviewAcceptance.model_validate_json(p.read_bytes())
    if data.acceptance_rate < 0.90:
        sys.stderr.write(
            f"FAIL: QUAL-02 acceptance_rate={data.acceptance_rate:.2f} "
            f"< 0.90 threshold; v1.0 ship-blocker. Either fix rejected "
            f"entries and re-sample, or escalate to v1.x --review-full.\n"
        )
        return 1
    return 0
