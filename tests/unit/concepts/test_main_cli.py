"""Unit tests for ``src/book_indexer/concepts/__main__.py`` (v2 symbolic).

Covers:
- ``_env_preflight`` rejection of bad determinism env.
- ``RunSummary.to_dict`` produces sorted-key output (telemetry determinism).
- ``argparse`` rejects ``auth-check`` (D-32: dropped) with exit code 2.
- ``cmd_build`` returns 1 when corpus / patterns paths are missing.
- (slow) ``cmd_build`` against the live corpus emits 30 artifacts.
- (slow) ``cmd_replay`` against the live corpus returns 0 byte-identical.

Plan v2-03 Task 3 update: v1 tests for ``auth-check`` /
``run_concept_discovery`` / ``_classify_failure`` / ``replay tripwire`` /
``--refresh-concepts`` are removed; the v2 build/replay subcommands replace
them. v2 has NO subprocess calls so monkeypatching ``invoke_one`` is gone.

requirements_addressed: CON-04, CON-06
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from book_indexer.concepts.__main__ import (
    DEFAULT_CORPUS,
    DEFAULT_PATTERNS,
    RunSummary,
    _emit_telemetry,
    _env_preflight,
    main,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# env preflight
# ---------------------------------------------------------------------------


def test_env_preflight_accepts_correct_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    _env_preflight()  # must not raise


def test_env_preflight_rejects_missing_pythonhash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    with pytest.raises(RuntimeError, match="PYTHONHASHSEED"):
        _env_preflight()


def test_env_preflight_rejects_missing_tz(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.delenv("TZ", raising=False)
    with pytest.raises(RuntimeError, match="TZ"):
        _env_preflight()


def test_env_preflight_rejects_missing_lcall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.delenv("LC_ALL", raising=False)
    with pytest.raises(RuntimeError, match="LC_ALL"):
        _env_preflight()


# ---------------------------------------------------------------------------
# RunSummary telemetry shape + determinism
# ---------------------------------------------------------------------------


def test_run_summary_to_dict_is_sorted() -> None:
    summary = RunSummary(
        wall_clock_s=1.234,
        per_pass_counts={"ner": 50, "doctrinal": 80, "noun_phrase": 800},
        per_chunk_counts={"ch5": 300, "ch1": 400, "ch3": 200},
        artifacts_written=15,
        provenance_written=15,
        failures=[],
    )
    d = summary.to_dict()
    # per_pass_counts and per_chunk_counts are sorted alphabetically.
    assert list(d["per_pass_counts"].keys()) == ["doctrinal", "ner", "noun_phrase"]
    assert list(d["per_chunk_counts"].keys()) == ["ch1", "ch3", "ch5"]
    assert d["wall_clock_s"] == 1.234
    assert d["artifacts_written"] == 15


def test_emit_telemetry_writes_sorted_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = RunSummary(
        wall_clock_s=1.0,
        per_pass_counts={"ner": 1},
        per_chunk_counts={"ch1": 1},
        artifacts_written=1,
        provenance_written=1,
        failures=[],
    )
    _emit_telemetry(summary)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    # Top-level keys are sorted (orjson OPT_SORT_KEYS).
    assert list(parsed.keys()) == sorted(parsed.keys())
    assert parsed["artifacts_written"] == 1


# ---------------------------------------------------------------------------
# argparse — auth-check removed; unknown subcommand exits 2
# ---------------------------------------------------------------------------


def test_unknown_subcommand_exits_2() -> None:
    """argparse rejects ``auth-check`` (D-32: dropped) with SystemExit(2)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["auth-check"])
    assert exc_info.value.code == 2


def test_no_args_exits_2() -> None:
    """argparse requires a subcommand (``required=True``)."""
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# build — missing-input failure paths (no slow tests)
# ---------------------------------------------------------------------------


def test_build_fails_when_corpus_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    rc = main(
        [
            "build",
            "--corpus",
            str(tmp_path / "nonexistent.sqlite"),
            "--output-dir",
            str(tmp_path / "out"),
            "--patterns",
            str(DEFAULT_PATTERNS),
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "corpus not found" in err


def test_build_fails_when_patterns_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    # Make the corpus path "exist" via tmp file so we can isolate the
    # patterns-missing branch from the corpus-missing branch.
    fake_corpus = tmp_path / "fake.sqlite"
    fake_corpus.write_bytes(b"")
    rc = main(
        [
            "build",
            "--corpus",
            str(fake_corpus),
            "--output-dir",
            str(tmp_path / "out"),
            "--patterns",
            str(tmp_path / "nonexistent.yaml"),
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "patterns not found" in err


def test_replay_fails_when_output_dir_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    rc = main(
        [
            "replay",
            "--corpus",
            str(DEFAULT_CORPUS),
            "--output-dir",
            str(tmp_path / "nonexistent"),
            "--patterns",
            str(DEFAULT_PATTERNS),
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "committed artifacts not found" in err


# ---------------------------------------------------------------------------
# Slow tests — real corpus
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_build_emits_30_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cold build against the live the reference corpus corpus writes 15 artifacts +
    15 provenance sidecars to the requested output dir."""
    if not DEFAULT_CORPUS.exists():
        pytest.skip(f"corpus not built: {DEFAULT_CORPUS}")
    if not DEFAULT_PATTERNS.exists():
        pytest.skip(f"doctrinal patterns absent: {DEFAULT_PATTERNS}")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    out = tmp_path / "concepts"
    rc = main(
        [
            "build",
            "--corpus",
            str(DEFAULT_CORPUS),
            "--output-dir",
            str(out),
            "--patterns",
            str(DEFAULT_PATTERNS),
        ]
    )
    assert rc == 0, capsys.readouterr().err
    artifacts = [p for p in out.glob("*.json") if not p.name.endswith(".provenance.json")]
    provenances = list(out.glob("*.provenance.json"))
    assert len(artifacts) == 15, [p.name for p in artifacts]
    assert len(provenances) == 15, [p.name for p in provenances]


@pytest.mark.slow
def test_build_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """build subcommand exits 0 when all 15 (pass × chapter) extractions
    succeed."""
    if not DEFAULT_CORPUS.exists():
        pytest.skip(f"corpus not built: {DEFAULT_CORPUS}")
    if not DEFAULT_PATTERNS.exists():
        pytest.skip(f"doctrinal patterns absent: {DEFAULT_PATTERNS}")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    out = tmp_path / "concepts"
    rc = main(
        [
            "build",
            "--corpus",
            str(DEFAULT_CORPUS),
            "--output-dir",
            str(out),
            "--patterns",
            str(DEFAULT_PATTERNS),
            "--chapters",
            "1,2",
        ]
    )
    assert rc == 0


@pytest.mark.slow
def test_replay_returns_zero_on_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``replay`` returns 0 when a fresh build matches the committed copy
    byte-for-byte (Lock #5)."""
    if not DEFAULT_CORPUS.exists():
        pytest.skip(f"corpus not built: {DEFAULT_CORPUS}")
    if not DEFAULT_PATTERNS.exists():
        pytest.skip(f"doctrinal patterns absent: {DEFAULT_PATTERNS}")
    monkeypatch.setenv("PYTHONHASHSEED", "0")
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    out = tmp_path / "concepts"
    # First, build into ``out`` (so there's a "committed" set to compare against).
    rc_build = main(
        [
            "build",
            "--corpus",
            str(DEFAULT_CORPUS),
            "--output-dir",
            str(out),
            "--patterns",
            str(DEFAULT_PATTERNS),
            "--chapters",
            "1",
        ]
    )
    assert rc_build == 0
    # Then replay — fresh build into a tmpdir must byte-match.
    rc_replay = main(
        [
            "replay",
            "--corpus",
            str(DEFAULT_CORPUS),
            "--output-dir",
            str(out),
            "--patterns",
            str(DEFAULT_PATTERNS),
            "--chapters",
            "1",
        ]
    )
    assert rc_replay == 0
