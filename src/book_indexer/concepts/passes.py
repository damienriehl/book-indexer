"""Phase 3a v2: pure-symbolic pass orchestration (replaces v1 ThreadPool).

See ``.planning/phases/03A-llm-concept-discovery-parallel-with-3b/03A-CONTEXT.md``
AMENDMENT v2 (D-21..D-33) for the architecture pivot from LLM-pass to
symbolic. This module orchestrates the three deterministic spaCy-driven
passes shipped by Plan v2-02 (``symbolic.py``) and writes one
``{pass_type}_{chunk_id}.json`` artifact + ``{pass_type}_{chunk_id}.provenance.json``
sidecar per (pass × chapter) tuple per D-28.

Public surface:
    PASS_ORDER, CallResult,
    compute_corpus_sha, compute_pattern_sha, compute_spacy_model_sha,
    build_provenance, write_pass_artifact,
    run_all_symbolic.

Determinism contract (Lock #5):
    * No threads, no subprocess, no time-dependent calls.
    * Deterministic iteration order: ``PASS_ORDER × chapters``.
    * orjson dumps with ``OPT_SORT_KEYS | OPT_INDENT_2`` for byte-stable
      artifact output.
    * Atomic write via tmp + ``os.replace`` (mirrors ``verify/ledger.py``).

Lock #3 (no Anthropic SDK imports) — none of ``anthropic`` /
``claude_agent_sdk`` are imported anywhere in this module. Verified by
``tests/invariants/test_no_anthropic_sdk_imports.py``.

requirements_addressed: CON-04 (multi-pass union substrate), CON-06
(deterministic, content-addressed-equivalent build artifacts).
"""
from __future__ import annotations

import hashlib
import os
import platform
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import orjson
import spacy
from spacy.language import Language

from .schema import ConceptDiscoveryResponse

# NOTE on the symbolic-pass imports: ``symbolic.py`` imports
# ``canonical_form_key`` from ``union.py``; ``union.py`` (legacy) imports
# ``PASS_ORDER`` and ``CallResult`` from THIS module. To avoid the resulting
# import cycle (``passes -> symbolic -> union -> passes``) we lazy-import
# the three pass functions inside ``run_all_symbolic``.

__all__ = [
    "PASS_ORDER",
    "CallResult",
    "build_provenance",
    "compute_corpus_sha",
    "compute_pattern_sha",
    "compute_spacy_model_sha",
    "run_all_symbolic",
    "write_pass_artifact",
]


# CONTEXT D-22: 3 passes (implicit dropped — symbolic only).
PASS_ORDER: tuple[str, ...] = ("noun_phrase", "doctrinal", "ner")

# Mirror ``verify/ledger.py`` and ``corpus_writer`` byte-determinism flags.
_LEDGER_OPTS = orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2


@dataclass(frozen=True)
class CallResult:
    """Result of a single ``(chunk_id, pass_type)`` extraction.

    Field names are preserved from the v1 LLM-orchestration shape so
    ``union.py::union_candidates`` keeps working without edits. For v2
    symbolic passes ``error`` is ``None`` on the success path; non-None
    only when extraction itself raised (model load failure, SQL bug).
    ``response`` carries the validated ``ConceptDiscoveryResponse`` on
    success and ``None`` on failure.
    """

    chunk_id: str
    pass_type: str
    response: ConceptDiscoveryResponse | None
    error: str | None = None


# ---------------------------------------------------------------------------
# Provenance SHAs — D-28 sidecar fields
# ---------------------------------------------------------------------------


def compute_corpus_sha(corpus_path: Path) -> str:
    """SHA-256 of the ``page_corpus.sqlite`` file bytes."""
    return hashlib.sha256(Path(corpus_path).read_bytes()).hexdigest()


def compute_pattern_sha(patterns_path: Path) -> str:
    """SHA-256 of the ``doctrinal_patterns.yaml`` file bytes."""
    return hashlib.sha256(Path(patterns_path).read_bytes()).hexdigest()


def compute_spacy_model_sha(nlp: Language) -> str:
    """SHA-256 of the canonical model metadata.

    Hashes ``nlp.meta`` (a dict carrying name/version/lang/pipeline/sha) with
    ``orjson.OPT_SORT_KEYS`` so the digest is stable across pip-cache layouts
    and across machines. We deliberately avoid hashing weight files on disk
    because spaCy's package layout differs between editable installs and
    site-packages installs; ``nlp.meta`` is the canonical model identity.
    """
    meta_bytes = orjson.dumps(nlp.meta, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(meta_bytes).hexdigest()


def build_provenance(
    *,
    pass_type: str,
    chunk_id: str,
    nlp: Language,
    corpus_sha: str,
    pattern_sha: str | None,
) -> dict:
    """Build the per-artifact provenance sidecar dict (D-28).

    The 9 keys correspond exactly to the D-28 contract:
    ``pass_type, chunk_id, spacy_version, spacy_model, spacy_model_sha,
    entity_ruler_pattern_sha, python_version, corpus_sha, frozen_timestamp``.

    ``entity_ruler_pattern_sha`` is the supplied ``pattern_sha`` for the
    ``doctrinal`` pass and ``None`` for ``noun_phrase`` / ``ner`` (those
    passes do not consume the ruler patterns directly — the same ``nlp``
    object simply happens to have a ruler attached).
    """
    return {
        "pass_type": pass_type,
        "chunk_id": chunk_id,
        "spacy_version": spacy.__version__,
        "spacy_model": nlp.meta.get("name", "en_core_web_lg"),
        "spacy_model_sha": compute_spacy_model_sha(nlp),
        "entity_ruler_pattern_sha": pattern_sha if pass_type == "doctrinal" else None,
        "python_version": platform.python_version(),
        "corpus_sha": corpus_sha,
        "frozen_timestamp": 0,
    }


# ---------------------------------------------------------------------------
# Artifact write — D-28 path scheme + atomic tmp+rename
# ---------------------------------------------------------------------------


def _atomic_write(path: Path, data: bytes) -> None:
    """Atomic write via tmp + ``os.replace`` (mirrors ``verify/ledger.py``).

    Writes to ``path.with_suffix(suffix + ".tmp")`` then renames in place.
    If a process is killed mid-write the destination is never partially
    written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_pass_artifact(
    response: ConceptDiscoveryResponse,
    output_dir: Path,
    provenance: dict,
) -> tuple[Path, Path]:
    """Write ``{pass_type}_{chunk_id}.json`` + ``.provenance.json`` (D-28).

    Both files use ``orjson.dumps(..., OPT_SORT_KEYS | OPT_INDENT_2)`` so
    successive runs with identical inputs produce byte-identical bytes
    (Lock #5 substrate). Each write is atomic.

    Returns ``(artifact_path, provenance_path)`` for caller bookkeeping.
    """
    output_dir = Path(output_dir)
    artifact_path = output_dir / f"{response.pass_type}_{response.chunk_id}.json"
    provenance_path = (
        output_dir / f"{response.pass_type}_{response.chunk_id}.provenance.json"
    )
    artifact_bytes = orjson.dumps(
        response.model_dump(mode="json"),
        option=_LEDGER_OPTS,
    )
    provenance_bytes = orjson.dumps(provenance, option=_LEDGER_OPTS)
    _atomic_write(artifact_path, artifact_bytes)
    _atomic_write(provenance_path, provenance_bytes)
    return artifact_path, provenance_path


# ---------------------------------------------------------------------------
# Orchestrator — synchronous (no ThreadPool — spaCy is fast enough)
# ---------------------------------------------------------------------------


def _pass_fn_table() -> dict[
    str, Callable[[sqlite3.Connection, int, Language], ConceptDiscoveryResponse]
]:
    """Lazy import to break the ``passes -> symbolic -> union -> passes`` cycle."""
    from .symbolic import extract_doctrinal, extract_ner, extract_noun_phrases

    return {
        "noun_phrase": extract_noun_phrases,
        "doctrinal": extract_doctrinal,
        "ner": extract_ner,
    }


def run_all_symbolic(
    conn: sqlite3.Connection,
    nlp_with_doctrinal: Language,
    output_dir: Path,
    *,
    chapters: tuple[int, ...] = (1, 2, 3, 4, 5),
    corpus_path: Path,
    doctrinal_patterns_path: Path,
) -> list[CallResult]:
    """Run all (pass × chapter) extractions and write D-28 artifacts.

    Synchronous, deterministic order: ``PASS_ORDER × chapters``. Per
    Phase 3a v2 RESEARCH §"Performance Expectations" (lines 1167-1180):
    spaCy is fast enough (~5s full-corpus across 5 chapters × 3 passes)
    that no ThreadPool is needed; the simpler synchronous loop also makes
    determinism trivial to reason about.

    On extraction failure, a ``CallResult`` with ``response=None`` and
    ``error=<typename>: <message>`` is appended; downstream callers may
    choose to treat one failed tuple as fatal or continue (parity with
    the v1 F-2 per-future isolation pattern).

    Args:
        conn: read-only connection to ``page_corpus.sqlite``.
        nlp_with_doctrinal: spaCy pipeline with EntityRuler installed
            (use :func:`book_indexer.concepts.symbolic.build_doctrinal_nlp`).
        output_dir: target directory for ``{pass_type}_{chunk_id}.json``
            and ``.provenance.json`` sidecars. Created if absent.
        chapters: which chapter numbers to extract (default 1..5).
        corpus_path: filesystem path to the SQLite corpus — used to
            compute the provenance ``corpus_sha``.
        doctrinal_patterns_path: filesystem path to
            ``doctrinal_patterns.yaml`` — used to compute the
            doctrinal-pass ``entity_ruler_pattern_sha``.

    Returns:
        List of ``CallResult`` in ``(pass × chapter)`` deterministic order
        (length == ``len(PASS_ORDER) * len(chapters)``).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_sha = compute_corpus_sha(corpus_path)
    pattern_sha = compute_pattern_sha(doctrinal_patterns_path)
    fn_table = _pass_fn_table()
    results: list[CallResult] = []
    for pass_type in PASS_ORDER:
        fn = fn_table[pass_type]
        for chapter in chapters:
            chunk_id = f"ch{chapter}"
            try:
                response = fn(conn, chapter, nlp_with_doctrinal)
            except Exception as exc:  # noqa: BLE001 — broad catch parity with v1 F-2
                results.append(
                    CallResult(
                        chunk_id=chunk_id,
                        pass_type=pass_type,
                        response=None,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            provenance = build_provenance(
                pass_type=pass_type,
                chunk_id=chunk_id,
                nlp=nlp_with_doctrinal,
                corpus_sha=corpus_sha,
                pattern_sha=pattern_sha,
            )
            write_pass_artifact(response, output_dir, provenance)
            results.append(
                CallResult(
                    chunk_id=chunk_id,
                    pass_type=pass_type,
                    response=response,
                    error=None,
                )
            )
    return results
