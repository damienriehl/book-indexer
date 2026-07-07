"""AUD-04 metadata block schema + factory.

Single source of truth for output metadata. Embedded as:
  - Markdown: leading HTML comment <!-- ... --> with key=value per line.
  - DOCX: built into core.xml's <dc:description> (RESEARCH §H-11 Approach 1).
  - Standalone: artifacts/audit/metadata.json (orjson OPT_SORT_KEYS|OPT_INDENT_2).

Pydantic Metadata is frozen=True + extra='forbid' — Lock #2. The
``built_at`` field is a ``Literal["1970-01-01T00:00:00Z"]`` sentinel for
Lock #5 byte-determinism (any other value would break two-runs-diff).

Per RESEARCH §H-11 source-of-truth references:
  - pdf_sha256: artifacts/index_tree.provenance.json (Phase 4)
  - pipeline_version: pyproject.toml [project.version] (Open Q2;
      Phase 6 bumps to "1.0.0" at release; Phase 5 reads "0.1.0" today)
  - index_tree_schema_version: IndexTree.schema_version field
  - eyecite_version, reporters_db_version, courts_db_version, spacy_version,
      spacy_model_sha, pymupdf_version: index_tree.provenance.json
  - python_docx_version: importlib.metadata.version("python-docx")
  - cli_version: best-effort `claude --version` capture; informational

requirements_addressed: AUD-04.
"""
from __future__ import annotations

import importlib.metadata
import json
import subprocess
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

try:
    # Python 3.11+ stdlib; project pins 3.12.
    import tomllib  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from .errors import MetadataValidationError

__all__ = ["Metadata", "build_metadata"]

# Lock #5 sentinel — frozen at Unix epoch for byte-determinism.
_FROZEN_BUILT_AT: Literal["1970-01-01T00:00:00Z"] = "1970-01-01T00:00:00Z"


class Metadata(BaseModel):
    """AUD-04 Pydantic schema. Lock #2 frozen + extra='forbid'."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pdf_sha256: Annotated[str, Field(min_length=64, max_length=64)]
    pipeline_version: Annotated[str, Field(min_length=1)]
    index_tree_schema_version: Annotated[str, Field(min_length=1)]
    eyecite_version: Annotated[str, Field(min_length=1)]
    reporters_db_version: Annotated[str, Field(min_length=1)]
    courts_db_version: Annotated[str, Field(min_length=1)]
    spacy_version: Annotated[str, Field(min_length=1)]
    spacy_model_sha: Annotated[str, Field(min_length=64, max_length=64)]
    pymupdf_version: Annotated[str, Field(min_length=1)]
    python_docx_version: Annotated[str, Field(min_length=1)]
    cli_version: Annotated[str, Field(min_length=1)]

    # Phase 7 OUT-05 — distinguishes the sections-only output variant from
    # the dual sections+pages variant so downstream Lock #5 byte-identity
    # tests can pin each variant separately. Default ``False`` → existing
    # ``index.md`` / ``index.docx`` outputs unchanged. Sections-only renderers
    # set this to ``True``.
    #
    # Field name retained for v1.0 metadata-schema backward-compat per
    # CONTEXT 07 Specifics line 596; rename to sections_only_variant deferred
    # to v1.x.
    pages_only_variant: bool = False

    # Lock #5 sentinel — frozen at "1970-01-01T00:00:00Z" for byte-determinism.
    built_at: Literal["1970-01-01T00:00:00Z"] = "1970-01-01T00:00:00Z"


def _read_pyproject_version(pyproject_path: Path = Path("pyproject.toml")) -> str:
    """Open Q2: pipeline_version = pyproject.toml [project.version].

    Phase 6 will bump to "1.0.0"; Phase 5 reads whatever is current.
    """
    if tomllib is None:
        raise MetadataValidationError("tomllib unavailable (require Python 3.11+)")
    if not pyproject_path.exists():
        raise MetadataValidationError(f"{pyproject_path} not found")
    try:
        with pyproject_path.open("rb") as f:
            data = tomllib.load(f)
    except Exception as e:  # tomllib.TOMLDecodeError or OSError
        raise MetadataValidationError(f"{pyproject_path} not parseable: {e}") from e
    try:
        return str(data["project"]["version"])
    except KeyError as e:
        raise MetadataValidationError(
            f"pyproject.toml missing project.version: {e}"
        ) from e


def _capture_cli_version() -> str:
    """Best-effort ``claude --version`` capture; falls back to ``'unknown'``.

    Informational only per RESEARCH §H-11 (Phase 3a's CLI used to
    generate the LLM concepts; not load-bearing for replay).
    """
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        line = (result.stdout or result.stderr or "").strip().splitlines()
        if line:
            return line[0]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


def build_metadata(
    index_tree_provenance_path: Path = Path("artifacts/index_tree.provenance.json"),
    pyproject_path: Path = Path("pyproject.toml"),
) -> Metadata:
    """Construct an AUD-04 Metadata instance from existing provenance files.

    Sources (RESEARCH §H-11 verbatim):
      - index_tree.provenance.json: pdf_sha256, eyecite_version,
        reporters_db_version, courts_db_version, spacy_version,
        spacy_model_sha, pymupdf_version (if present), index_tree_schema_version
      - pyproject.toml: pipeline_version
      - importlib.metadata: python_docx_version
      - subprocess: cli_version (informational; falls back to 'unknown')

    Raises:
        MetadataValidationError: if a required source is missing or malformed.
    """
    if not index_tree_provenance_path.exists():
        raise MetadataValidationError(
            f"{index_tree_provenance_path} not found — run "
            f"`python -m book_indexer.assembly build` first"
        )
    try:
        prov = json.loads(index_tree_provenance_path.read_text())
    except json.JSONDecodeError as e:
        raise MetadataValidationError(
            f"{index_tree_provenance_path} not valid JSON: {e}"
        ) from e

    # index_tree_schema_version lives on IndexTree, but Phase 4's provenance
    # also pins it (RESEARCH §H-11 — Phase 5 reads provenance, not the IR
    # body, to keep this function fast).
    try:
        return Metadata(
            pdf_sha256=prov["pdf_sha256"],
            pipeline_version=_read_pyproject_version(pyproject_path),
            index_tree_schema_version=prov.get("index_tree_schema_version", "1.0"),
            eyecite_version=prov["eyecite_version"],
            reporters_db_version=prov["reporters_db_version"],
            courts_db_version=prov["courts_db_version"],
            spacy_version=prov["spacy_version"],
            spacy_model_sha=prov["spacy_model_sha"],
            pymupdf_version=prov.get("pymupdf_version", "1.27.2.2"),
            python_docx_version=importlib.metadata.version("python-docx"),
            cli_version=_capture_cli_version(),
        )
    except KeyError as e:
        raise MetadataValidationError(
            f"{index_tree_provenance_path} missing field {e.args[0]!r}"
        ) from e
