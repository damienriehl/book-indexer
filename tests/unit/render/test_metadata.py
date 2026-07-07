"""Unit tests for src/book_indexer/render/metadata.py.

Locks AUD-04 Pydantic schema + build_metadata() failure modes:
  - Lock #2: frozen=True + extra='forbid'.
  - Lock #5: built_at is Literal['1970-01-01T00:00:00Z'] sentinel.
  - SHA fields enforce exactly 64 chars (min+max=64).
  - JSON round-trip is byte-equal.
  - build_metadata() raises MetadataValidationError on every documented
    failure mode (missing provenance, malformed JSON, missing key,
    missing pyproject).
  - cli_version capture is fault-tolerant (never raises).
  - Live integration smoke against artifacts/index_tree.provenance.json
    (skipif file absent).

requirements_addressed: AUD-04.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from book_indexer.render import Metadata, build_metadata
from book_indexer.render.errors import MetadataValidationError
from book_indexer.render.metadata import (
    _capture_cli_version,
    _read_pyproject_version,
)

_VALID_SHA = "a" * 64
_LIVE_PROVENANCE = Path("artifacts/index_tree.provenance.json")


# ---------------------------------------------------------------------------
# Pydantic Metadata — Lock #2 (frozen + extra='forbid') + Lock #5 (built_at)
# ---------------------------------------------------------------------------


def test_metadata_constructs_from_fixture(frozen_metadata: Metadata) -> None:
    assert frozen_metadata.pdf_sha256 == (
        "94503c16dcc3ce29d64be1591fec85eb94b83c4452622cfd9676bb604e553070"
    )
    assert frozen_metadata.built_at == "1970-01-01T00:00:00Z"


def test_metadata_rejects_unknown_fields() -> None:
    """Lock #2: extra='forbid' — unknown fields raise ValidationError."""
    with pytest.raises(ValidationError):
        Metadata(
            pdf_sha256=_VALID_SHA,
            pipeline_version="0.1.0",
            index_tree_schema_version="1.0",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.64",
            courts_db_version="0.10.27",
            spacy_version="3.8.14",
            spacy_model_sha=_VALID_SHA,
            pymupdf_version="1.27.2.2",
            python_docx_version="1.2.0",
            cli_version="2.1.119",
            extra_field="bogus",  # type: ignore[call-arg]
        )


def test_metadata_is_frozen(frozen_metadata: Metadata) -> None:
    """Lock #2: frozen=True — mutation raises ValidationError."""
    with pytest.raises(ValidationError):
        frozen_metadata.pipeline_version = "9.9.9"  # type: ignore[misc]


def test_metadata_built_at_must_be_sentinel() -> None:
    """Lock #5: built_at is Literal['1970-01-01T00:00:00Z'] —
    any other value rejected."""
    with pytest.raises(ValidationError):
        Metadata(
            pdf_sha256=_VALID_SHA,
            pipeline_version="0.1.0",
            index_tree_schema_version="1.0",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.64",
            courts_db_version="0.10.27",
            spacy_version="3.8.14",
            spacy_model_sha=_VALID_SHA,
            pymupdf_version="1.27.2.2",
            python_docx_version="1.2.0",
            cli_version="2.1.119",
            built_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        )


def test_metadata_built_at_default_is_sentinel() -> None:
    m = Metadata(
        pdf_sha256=_VALID_SHA,
        pipeline_version="0.1.0",
        index_tree_schema_version="1.0",
        eyecite_version="2.7.6",
        reporters_db_version="3.2.64",
        courts_db_version="0.10.27",
        spacy_version="3.8.14",
        spacy_model_sha=_VALID_SHA,
        pymupdf_version="1.27.2.2",
        python_docx_version="1.2.0",
        cli_version="2.1.119",
    )
    assert m.built_at == "1970-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# SHA length validators — exactly 64 chars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_sha", ["a" * 63, "a" * 65, "", "deadbeef"])
def test_metadata_rejects_invalid_pdf_sha256_length(bad_sha: str) -> None:
    with pytest.raises(ValidationError):
        Metadata(
            pdf_sha256=bad_sha,
            pipeline_version="0.1.0",
            index_tree_schema_version="1.0",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.64",
            courts_db_version="0.10.27",
            spacy_version="3.8.14",
            spacy_model_sha=_VALID_SHA,
            pymupdf_version="1.27.2.2",
            python_docx_version="1.2.0",
            cli_version="2.1.119",
        )


@pytest.mark.parametrize("bad_sha", ["a" * 63, "a" * 65, "", "deadbeef"])
def test_metadata_rejects_invalid_spacy_model_sha_length(bad_sha: str) -> None:
    with pytest.raises(ValidationError):
        Metadata(
            pdf_sha256=_VALID_SHA,
            pipeline_version="0.1.0",
            index_tree_schema_version="1.0",
            eyecite_version="2.7.6",
            reporters_db_version="3.2.64",
            courts_db_version="0.10.27",
            spacy_version="3.8.14",
            spacy_model_sha=bad_sha,
            pymupdf_version="1.27.2.2",
            python_docx_version="1.2.0",
            cli_version="2.1.119",
        )


def test_metadata_accepts_exactly_64_char_shas() -> None:
    m = Metadata(
        pdf_sha256=_VALID_SHA,
        pipeline_version="0.1.0",
        index_tree_schema_version="1.0",
        eyecite_version="2.7.6",
        reporters_db_version="3.2.64",
        courts_db_version="0.10.27",
        spacy_version="3.8.14",
        spacy_model_sha="b" * 64,
        pymupdf_version="1.27.2.2",
        python_docx_version="1.2.0",
        cli_version="2.1.119",
    )
    assert len(m.pdf_sha256) == 64
    assert len(m.spacy_model_sha) == 64


# ---------------------------------------------------------------------------
# JSON round-trip — locks serialization shape (Lock #5 byte-identity)
# ---------------------------------------------------------------------------


def test_metadata_json_round_trip_byte_equal(frozen_metadata: Metadata) -> None:
    payload = frozen_metadata.model_dump_json()
    restored = Metadata.model_validate_json(payload)
    assert restored == frozen_metadata
    assert restored.model_dump_json() == payload


# ---------------------------------------------------------------------------
# _read_pyproject_version
# ---------------------------------------------------------------------------


def test_read_pyproject_version_live_file() -> None:
    """The live pyproject.toml must yield a non-empty version string."""
    version = _read_pyproject_version(Path("pyproject.toml"))
    assert isinstance(version, str)
    assert version
    # Sanity: matches semver-ish prefix.
    assert version[0].isdigit()


def test_read_pyproject_version_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.toml"
    with pytest.raises(MetadataValidationError):
        _read_pyproject_version(missing)


def test_read_pyproject_version_missing_project_table(tmp_path: Path) -> None:
    """A pyproject.toml without [project].version raises
    MetadataValidationError (not generic KeyError)."""
    fake = tmp_path / "pyproject.toml"
    fake.write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    with pytest.raises(MetadataValidationError):
        _read_pyproject_version(fake)


# ---------------------------------------------------------------------------
# build_metadata — failure modes
# ---------------------------------------------------------------------------


def test_build_metadata_missing_provenance_raises(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_provenance.json"
    with pytest.raises(MetadataValidationError):
        build_metadata(index_tree_provenance_path=missing)


def test_build_metadata_malformed_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "provenance.json"
    bad.write_text("not valid json {{{", encoding="utf-8")
    with pytest.raises(MetadataValidationError):
        build_metadata(index_tree_provenance_path=bad)


def test_build_metadata_missing_key_raises(tmp_path: Path) -> None:
    """Provenance JSON missing 'eyecite_version' raises
    MetadataValidationError (NOT a generic KeyError)."""
    stub = {
        "pdf_sha256": _VALID_SHA,
        # eyecite_version intentionally absent
        "reporters_db_version": "3.2.64",
        "courts_db_version": "0.10.27",
        "spacy_version": "3.8.14",
        "spacy_model_sha": _VALID_SHA,
    }
    p = tmp_path / "provenance.json"
    p.write_text(json.dumps(stub), encoding="utf-8")
    with pytest.raises(MetadataValidationError) as excinfo:
        build_metadata(index_tree_provenance_path=p)
    assert "eyecite_version" in str(excinfo.value)


def test_build_metadata_missing_pyproject_raises(tmp_path: Path) -> None:
    """If pyproject.toml is missing, build_metadata raises
    MetadataValidationError (NOT a generic FileNotFoundError)."""
    stub = {
        "pdf_sha256": _VALID_SHA,
        "eyecite_version": "2.7.6",
        "reporters_db_version": "3.2.64",
        "courts_db_version": "0.10.27",
        "spacy_version": "3.8.14",
        "spacy_model_sha": _VALID_SHA,
    }
    p = tmp_path / "provenance.json"
    p.write_text(json.dumps(stub), encoding="utf-8")
    missing_pyproject = tmp_path / "no_pyproject.toml"
    with pytest.raises(MetadataValidationError):
        build_metadata(
            index_tree_provenance_path=p,
            pyproject_path=missing_pyproject,
        )


# ---------------------------------------------------------------------------
# Live-integration smoke (skipif provenance absent — robust pre-Wave-4)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _LIVE_PROVENANCE.exists(),
    reason="artifacts/index_tree.provenance.json absent (pre-Wave-4 Phase 5 cold-build)",
)
def test_build_metadata_live_provenance_smoke() -> None:
    """Smoke: build_metadata() succeeds against the live Phase 4 cold-build
    provenance file. Exercises the full read/parse/construct path without
    a Lock #5 sentinel violation."""
    m = build_metadata()
    assert isinstance(m, Metadata)
    assert m.built_at == "1970-01-01T00:00:00Z"
    assert len(m.pdf_sha256) == 64
    assert len(m.spacy_model_sha) == 64
    assert m.python_docx_version  # importlib.metadata captured something


# ---------------------------------------------------------------------------
# _capture_cli_version — fault-tolerant (never raises)
# ---------------------------------------------------------------------------


def test_capture_cli_version_returns_string() -> None:
    """_capture_cli_version returns a non-empty string regardless of
    whether `claude --version` is on PATH (timeout/missing → 'unknown')."""
    version = _capture_cli_version()
    assert isinstance(version, str)
    assert version  # never empty


def test_capture_cli_version_never_raises_on_missing_binary(monkeypatch) -> None:
    """If `claude` is not on PATH, the function returns 'unknown' rather
    than propagating FileNotFoundError."""
    import subprocess as _subprocess

    def boom(*args, **kwargs):
        raise FileNotFoundError("claude not found")

    monkeypatch.setattr(_subprocess, "run", boom)
    assert _capture_cli_version() == "unknown"


def test_capture_cli_version_never_raises_on_timeout(monkeypatch) -> None:
    import subprocess as _subprocess

    def boom(*args, **kwargs):
        raise _subprocess.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(_subprocess, "run", boom)
    assert _capture_cli_version() == "unknown"
