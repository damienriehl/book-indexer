"""Unit tests for ``src/book_indexer/concepts/cache.py``.

requirements_addressed: CON-06
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import orjson
import pytest

from book_indexer.concepts import (
    CacheKeyDriftError,
    ConceptCandidate,
    ConceptDiscoveryResponse,
)
from book_indexer.concepts.cache import (
    CACHE_ROOT,
    cache_key,
    cache_path,
    provenance_dict,
    provenance_path,
    read_cache,
    write_cache,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_response() -> ConceptDiscoveryResponse:
    return ConceptDiscoveryResponse(
        schema_version="1",
        pass_type="doctrinal",
        chunk_id="ch1",
        candidates=[
            ConceptCandidate(
                term="hearsay",
                canonical_form="hearsay",
                variants=[],
                example_quote="an out-of-court statement",
            ),
        ],
    )


@pytest.fixture
def sample_provenance() -> dict[str, Any]:
    return provenance_dict(
        sha="deadbeef" * 8,
        subprocess_args=["claude", "-p", "--output-format", "json"],
        claude_cli_version="2.1.119",
        claude_cli_stderr_tail="",
        duration_ms=9823,
        prompt_filename="concept_discovery_doctrinal_v1.md",
        prompt_version="1",
        pass_type="doctrinal",
        chunk_id="ch1",
        model_name="claude-sonnet-4-6",
        schema_version="1",
    )


# ---------------------------------------------------------------------------
# Key composition — the 4-factor sensitivity matrix
# ---------------------------------------------------------------------------


def test_cache_key_deterministic() -> None:
    a = cache_key("noun_phrase_v1", "noun_phrase", "claude-sonnet-4-6", "chunk body")
    b = cache_key("noun_phrase_v1", "noun_phrase", "claude-sonnet-4-6", "chunk body")
    assert a == b
    assert len(a) == 64  # hex sha256
    assert all(c in "0123456789abcdef" for c in a)


def test_cache_key_sensitive_prompt_version() -> None:
    a = cache_key("noun_phrase_v1", "noun_phrase", "m", "x")
    b = cache_key("noun_phrase_v2", "noun_phrase", "m", "x")
    assert a != b


def test_cache_key_sensitive_pass_type() -> None:
    a = cache_key("v1", "noun_phrase", "m", "x")
    b = cache_key("v1", "doctrinal", "m", "x")
    assert a != b


def test_cache_key_sensitive_model_name() -> None:
    """D-14 rationale: model upgrade (Sonnet 4.6 → 4.7) must invalidate cache."""
    a = cache_key("v1", "doctrinal", "claude-sonnet-4-6", "x")
    b = cache_key("v1", "doctrinal", "claude-sonnet-4-7", "x")
    assert a != b


def test_cache_key_sensitive_chunk_text() -> None:
    a = cache_key("v1", "doctrinal", "m", "the quick brown fox")
    b = cache_key("v1", "doctrinal", "m", "the quick brown foz")
    assert a != b


def test_cache_key_delimiter_prevents_stem_collision() -> None:
    """||-separator safety margin — research §F-3."""
    a = cache_key("v1", "noun_phrase", "m", "x")
    b = cache_key("v1n", "oun_phrase", "m", "x")
    assert a != b


def test_cache_key_hex_stable_across_processes() -> None:
    """sha256 is deterministic; this is a sanity smoke against accidental randomness."""
    material = b"v1||noun_phrase||claude-sonnet-4-6||hello"
    expected = hashlib.sha256(material).hexdigest()
    assert cache_key("v1", "noun_phrase", "claude-sonnet-4-6", "hello") == expected


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_cache_path_and_provenance_path() -> None:
    sha = "abc" * 21 + "d"  # 64 chars
    assert cache_path(sha) == CACHE_ROOT / f"{sha}.json"
    assert provenance_path(sha) == CACHE_ROOT / f"{sha}.provenance.json"


def test_cache_path_root_override(tmp_path: Path) -> None:
    sha = "deadbeef" * 8
    assert cache_path(sha, root=tmp_path) == tmp_path / f"{sha}.json"
    assert provenance_path(sha, root=tmp_path) == tmp_path / f"{sha}.provenance.json"


# ---------------------------------------------------------------------------
# Provenance sidecar — forbidden fields
# ---------------------------------------------------------------------------


def test_provenance_dict_excludes_forbidden_fields(sample_provenance: dict[str, Any]) -> None:
    """research §F-3: cost / session_id / uuid / real timestamps WOULD defeat
    byte-deterministic diffs of the provenance file."""
    forbidden = {"cost", "session_id", "uuid", "total_cost_usd"}
    assert not (forbidden & set(sample_provenance.keys()))
    assert sample_provenance["timestamp_frozen"] == 0


def test_provenance_dict_truncates_stderr_tail() -> None:
    long_stderr = "X" * 5000
    p = provenance_dict(
        sha="0" * 64,
        subprocess_args=["claude", "-p"],
        claude_cli_version="2.1.119",
        claude_cli_stderr_tail=long_stderr,
        duration_ms=1,
        prompt_filename="x.md",
        prompt_version="1",
        pass_type="doctrinal",
        chunk_id="ch1",
        model_name="claude-sonnet-4-6",
        schema_version="1",
    )
    assert len(p["claude_cli_stderr_tail"]) == 2000


# ---------------------------------------------------------------------------
# Atomic write + round-trip
# ---------------------------------------------------------------------------


def test_write_cache_round_trip(
    tmp_path: Path,
    sample_response: ConceptDiscoveryResponse,
    sample_provenance: dict[str, Any],
) -> None:
    sha = sample_provenance["cache_key"]
    write_cache(sha, sample_response, sample_provenance, root=tmp_path)
    assert (tmp_path / f"{sha}.json").is_file()
    assert (tmp_path / f"{sha}.provenance.json").is_file()

    entry = read_cache(sha, root=tmp_path)
    assert entry.response == sample_response
    assert entry.provenance == sample_provenance


def test_write_cache_byte_identical_two_runs(
    tmp_path: Path,
    sample_response: ConceptDiscoveryResponse,
    sample_provenance: dict[str, Any],
) -> None:
    """Same response → byte-identical file content across two writes."""
    sha = sample_provenance["cache_key"]

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    write_cache(sha, sample_response, sample_provenance, root=dir_a)
    write_cache(sha, sample_response, sample_provenance, root=dir_b)

    assert (dir_a / f"{sha}.json").read_bytes() == (dir_b / f"{sha}.json").read_bytes()
    assert (dir_a / f"{sha}.provenance.json").read_bytes() == (
        dir_b / f"{sha}.provenance.json"
    ).read_bytes()


def test_read_cache_raises_on_sha_drift(
    tmp_path: Path,
    sample_response: ConceptDiscoveryResponse,
    sample_provenance: dict[str, Any],
) -> None:
    sha = sample_provenance["cache_key"]
    write_cache(sha, sample_response, sample_provenance, root=tmp_path)

    # Hand-edit the provenance file to claim a different sha.
    prov_path = tmp_path / f"{sha}.provenance.json"
    tampered = orjson.loads(prov_path.read_bytes())
    tampered["cache_key"] = "0" * 64
    prov_path.write_bytes(
        orjson.dumps(tampered, option=orjson.OPT_SORT_KEYS | orjson.OPT_INDENT_2)
    )

    with pytest.raises(CacheKeyDriftError):
        read_cache(sha, root=tmp_path)


def test_read_cache_missing_response_raises(
    tmp_path: Path,
    sample_provenance: dict[str, Any],
) -> None:
    with pytest.raises(FileNotFoundError):
        read_cache(sample_provenance["cache_key"], root=tmp_path)


def test_write_cache_atomic_no_torn_file_on_exception(
    tmp_path: Path,
    sample_provenance: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force ``os.replace`` to raise; assert no half-written file remains."""
    from book_indexer.concepts import cache as cache_mod

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(cache_mod.os, "replace", boom)

    sha = sample_provenance["cache_key"]
    bogus_response = ConceptDiscoveryResponse(
        schema_version="1", pass_type="doctrinal", chunk_id="ch1", candidates=[]
    )
    with pytest.raises(OSError, match="simulated mid-write failure"):
        write_cache(sha, bogus_response, sample_provenance, root=tmp_path)

    # No .json and no .tmp file should remain.
    assert not (tmp_path / f"{sha}.json").exists()
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"torn tmp files left behind: {leftovers}"


def test_output_bytes_are_lf_only(
    tmp_path: Path,
    sample_response: ConceptDiscoveryResponse,
    sample_provenance: dict[str, Any],
) -> None:
    """orjson never emits CR; verify defensively so a future serializer swap
    can't silently break cross-platform determinism (H-4 sibling concern)."""
    sha = sample_provenance["cache_key"]
    write_cache(sha, sample_response, sample_provenance, root=tmp_path)
    assert b"\r" not in (tmp_path / f"{sha}.json").read_bytes()
    assert b"\r" not in (tmp_path / f"{sha}.provenance.json").read_bytes()
