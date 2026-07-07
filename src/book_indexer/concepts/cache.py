"""Content-addressed cache for Phase-3a subagent responses.

Cache contract (CON-06 / 03A-CONTEXT.md D-14 / D-15):

- **Key**: ``sha256("||".join([prompt_version, pass_type, model_name, chunk_text]))``
  The ``||`` separator prevents stem collisions (e.g. ``v1`` + ``noun_phrase``
  vs. ``v1n`` + ``oun_phrase`` — both yield different sha). Expands CON-06's
  literal ``chunk_text + prompt_version`` minimum because a model upgrade
  (Sonnet 4.6 → 4.7) otherwise silently reuses stale cache (D-14 rationale).
- **Files**: ``artifacts/cache/candidates/<sha>.json`` (the Pydantic-dumped
  ``ConceptDiscoveryResponse``) + ``artifacts/cache/candidates/<sha>.provenance.json``
  (audit metadata). Both committed to git.
- **Not in key**: CLI version, max_turns, timeout, effort — these live in the
  provenance sidecar only; they would over-invalidate cache on routine CLI
  patches (D-14).
- **Writes**: atomic (``tempfile.mkstemp`` → write → ``os.replace``) mirroring
  Phase 2's ``verify/ledger.py`` precedent.
- **Serialization**: ``orjson.dumps(..., OPT_SORT_KEYS | OPT_INDENT_2)`` —
  identical to Phase 1 ``_json`` and Phase 2 ``_LEDGER_OPTS``.

requirements_addressed: CON-06
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from .errors import CacheKeyDriftError
from .schema import ConceptDiscoveryResponse

__all__ = [
    "CACHE_ROOT",
    "ConceptCacheEntry",
    "cache_key",
    "cache_path",
    "provenance_dict",
    "provenance_path",
    "read_cache",
    "write_cache",
]


# Relative path resolved against repo root by callers; absolute path returned
# by the helpers below is relative-to-caller's cwd. Callers pass the repo root
# explicitly when writing (see __main__.py in Plan 03A-08).
CACHE_ROOT = Path("artifacts/cache/candidates")

_ORJSON_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConceptCacheEntry:
    """Round-trip result of ``read_cache`` — both files loaded."""

    response: ConceptDiscoveryResponse
    provenance: dict[str, Any]


# ---------------------------------------------------------------------------
# Key composition — the ONE thing the cache MUST get right
# ---------------------------------------------------------------------------


def cache_key(
    prompt_version: str, pass_type: str, model_name: str, chunk_text: str
) -> str:
    """D-14 cache key.

    Args:
        prompt_version: version stem extracted from the prompt filename,
            e.g. ``"noun_phrase_v1"`` — per D-17,
            ``prompt_filename.stem.rsplit("_v", 1)`` → joined with ``"_v"``.
        pass_type: one of ``"noun_phrase" | "doctrinal" | "ner" | "implicit"``.
        model_name: exact string passed to ``--model``, e.g.
            ``"claude-sonnet-4-6"``.
        chunk_text: the canonical body prose for this chunk, as built by
            ``chunker.build_chunk_text`` (H-4 byte-stable).

    Returns:
        Hex sha256 string (64 chars).

    The ``||`` separator is a safety margin against stem collisions — see
    03A-RESEARCH.md §F-3 "How to spot a bad implementation".
    """
    material = f"{prompt_version}||{pass_type}||{model_name}||{chunk_text}".encode()
    return hashlib.sha256(material).hexdigest()


def cache_path(sha: str, root: Path | None = None) -> Path:
    """``<root>/<sha>.json`` — flat-directory layout (D-15)."""
    base = root if root is not None else CACHE_ROOT
    return base / f"{sha}.json"


def provenance_path(sha: str, root: Path | None = None) -> Path:
    """``<root>/<sha>.provenance.json`` — sibling of the response file."""
    base = root if root is not None else CACHE_ROOT
    return base / f"{sha}.provenance.json"


# ---------------------------------------------------------------------------
# Provenance sidecar — every field except determinism-poisoning ones
# ---------------------------------------------------------------------------


def provenance_dict(
    *,
    sha: str,
    subprocess_args: list[str],
    claude_cli_version: str,
    claude_cli_stderr_tail: str,
    duration_ms: int,
    prompt_filename: str,
    prompt_version: str,
    pass_type: str,
    chunk_id: str,
    model_name: str,
    schema_version: str,
) -> dict[str, Any]:
    """Build the provenance sidecar dict (D-15).

    Fields EXCLUDED from the sidecar intentionally: ``cost``, ``session_id``,
    ``uuid``, ``total_cost_usd``, any real timestamp. Including them would
    make the sidecar bytes non-deterministic across runs — the file would
    show up in ``git status`` on every rebuild even when nothing material
    changed (research §F-3).
    """
    return {
        "cache_key": sha,
        "claude_cli_stderr_tail": claude_cli_stderr_tail[-2000:] if claude_cli_stderr_tail else "",
        "claude_cli_version": claude_cli_version,
        "chunk_id": chunk_id,
        "duration_ms": int(duration_ms),
        "model_name": model_name,
        "pass_type": pass_type,
        "prompt_filename": prompt_filename,
        "prompt_version": prompt_version,
        "schema_version": schema_version,
        "subprocess_args": list(subprocess_args),
        "timestamp_frozen": 0,
    }


# ---------------------------------------------------------------------------
# Atomic write — mirror of verify/ledger.py:73-95
# ---------------------------------------------------------------------------


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Atomic: tmp file in same dir, ``os.replace`` rename. POSIX-atomic on
    the same filesystem; prevents half-written files under kill-9."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def write_cache(
    sha: str,
    response: ConceptDiscoveryResponse,
    provenance: dict[str, Any],
    root: Path | None = None,
) -> None:
    """Atomically write both ``<sha>.json`` and ``<sha>.provenance.json``.

    Writes the response first, then the provenance — so if the run is killed
    mid-operation, either BOTH files exist (success state) or the response
    exists without provenance (recoverable: rerunning the call rebuilds the
    sidecar). The reverse ordering would leave us with provenance-without-response,
    which would fail ``read_cache`` on the next build.
    """
    resp_bytes = orjson.dumps(response.model_dump(mode="json"), option=_ORJSON_OPTS)
    prov_bytes = orjson.dumps(provenance, option=_ORJSON_OPTS)

    _atomic_write_bytes(cache_path(sha, root), resp_bytes)
    _atomic_write_bytes(provenance_path(sha, root), prov_bytes)


def read_cache(sha: str, root: Path | None = None) -> ConceptCacheEntry:
    """Load both files; validate them; check the sha self-link.

    Raises:
        FileNotFoundError: if either file is absent.
        pydantic.ValidationError: if the response file does not match
            ``ConceptDiscoveryResponse``.
        CacheKeyDriftError: if the provenance's ``cache_key`` field does
            not equal ``sha`` — defensive check against hand-edited files.
    """
    resp_bytes = cache_path(sha, root).read_bytes()
    prov_bytes = provenance_path(sha, root).read_bytes()
    response = ConceptDiscoveryResponse.model_validate_json(resp_bytes)
    provenance: dict[str, Any] = orjson.loads(prov_bytes)
    claimed_sha = provenance.get("cache_key")
    if claimed_sha != sha:
        raise CacheKeyDriftError(
            f"cache_key drift: provenance claims {claimed_sha!r} but filename is {sha!r}"
        )
    return ConceptCacheEntry(response=response, provenance=provenance)
