"""Unit tests for ``src/book_indexer/concepts/passes.py`` (v2 symbolic).

Plan v2-03 Task 4 update: the v1 LLM-orchestration tests
(``run_all_passes`` warm-cache / cache-miss / failure-isolation /
ordering / mixed-cache / load_prompt_body / system_prompt) are
gone. v2 passes is a deterministic synchronous orchestrator: this
suite locks PASS_ORDER, ``CallResult`` dataclass shape, the three
SHA helpers, ``build_provenance`` D-28 sidecar shape, the atomic
``write_pass_artifact`` byte-determinism contract, and a slow
``run_all_symbolic`` real-corpus integration test.

requirements_addressed: CON-04, CON-06
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import spacy

from book_indexer.concepts.passes import (
    PASS_ORDER,
    CallResult,
    build_provenance,
    compute_corpus_sha,
    compute_pattern_sha,
    compute_spacy_model_sha,
    run_all_symbolic,
    write_pass_artifact,
)
from book_indexer.concepts.schema import (
    ConceptCandidate,
    ConceptDiscoveryResponse,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# PASS_ORDER + CallResult shape
# ---------------------------------------------------------------------------


def test_pass_order_is_three_v2_passes() -> None:
    assert PASS_ORDER == ("noun_phrase", "doctrinal", "ner")
    assert isinstance(PASS_ORDER, tuple)
    assert "implicit" not in PASS_ORDER


def test_call_result_dataclass_shape() -> None:
    """CallResult preserves v1 field NAMES (chunk_id, pass_type, response,
    error) so union.py keeps working; ``error`` defaults to None."""
    r = CallResult(chunk_id="ch1", pass_type="noun_phrase", response=None)
    assert r.chunk_id == "ch1"
    assert r.pass_type == "noun_phrase"
    assert r.response is None
    assert r.error is None  # default


def test_call_result_is_frozen() -> None:
    r = CallResult(chunk_id="ch1", pass_type="noun_phrase", response=None)
    with pytest.raises((AttributeError, TypeError)):
        r.chunk_id = "ch2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SHA helpers
# ---------------------------------------------------------------------------


def test_compute_corpus_sha_is_deterministic(tmp_path: Path) -> None:
    blob = b"sqlite-bytes-here\x00\x01\x02"
    f = tmp_path / "corpus.sqlite"
    f.write_bytes(blob)
    sha1 = compute_corpus_sha(f)
    sha2 = compute_corpus_sha(f)
    assert sha1 == sha2
    assert sha1 == hashlib.sha256(blob).hexdigest()


def test_compute_pattern_sha_is_deterministic(tmp_path: Path) -> None:
    yaml_text = b"patterns:\n  - id: foo\n    label: BAR\n    pattern: []\n"
    f = tmp_path / "patterns.yaml"
    f.write_bytes(yaml_text)
    sha1 = compute_pattern_sha(f)
    sha2 = compute_pattern_sha(f)
    assert sha1 == sha2
    assert sha1 == hashlib.sha256(yaml_text).hexdigest()


@pytest.mark.slow
def test_compute_spacy_model_sha_is_deterministic(nlp) -> None:
    """Hashing the same nlp.meta twice yields the same hex digest."""
    sha1 = compute_spacy_model_sha(nlp)
    sha2 = compute_spacy_model_sha(nlp)
    assert sha1 == sha2
    assert len(sha1) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# build_provenance — D-28 sidecar contract
# ---------------------------------------------------------------------------


def _fake_nlp() -> spacy.language.Language:
    """Tiny ``Language`` instance whose ``meta`` matches the en_core_web_lg
    public shape — sufficient for build_provenance's call to
    ``compute_spacy_model_sha`` (which only reads ``nlp.meta``)."""
    nlp = spacy.blank("en")
    # Rewrite meta to look like a real loaded model so spacy_model is stable.
    nlp.meta["name"] = "en_core_web_lg"
    nlp.meta["version"] = "3.8.0"
    return nlp


def test_build_provenance_has_required_keys() -> None:
    nlp = _fake_nlp()
    prov = build_provenance(
        pass_type="noun_phrase",
        chunk_id="ch1",
        nlp=nlp,
        corpus_sha="a" * 64,
        pattern_sha="b" * 64,
    )
    expected = {
        "pass_type",
        "chunk_id",
        "spacy_version",
        "spacy_model",
        "spacy_model_sha",
        "entity_ruler_pattern_sha",
        "python_version",
        "corpus_sha",
        "frozen_timestamp",
    }
    assert set(prov.keys()) == expected
    assert prov["frozen_timestamp"] == 0
    assert prov["corpus_sha"] == "a" * 64
    assert prov["spacy_model"] == "en_core_web_lg"


def test_build_provenance_pattern_sha_only_for_doctrinal() -> None:
    """``entity_ruler_pattern_sha`` is the supplied value for ``doctrinal``
    and ``None`` for the other two passes (D-28)."""
    nlp = _fake_nlp()
    prov_doc = build_provenance(
        pass_type="doctrinal",
        chunk_id="ch1",
        nlp=nlp,
        corpus_sha="a" * 64,
        pattern_sha="b" * 64,
    )
    assert prov_doc["entity_ruler_pattern_sha"] == "b" * 64

    prov_np = build_provenance(
        pass_type="noun_phrase",
        chunk_id="ch1",
        nlp=nlp,
        corpus_sha="a" * 64,
        pattern_sha="b" * 64,
    )
    assert prov_np["entity_ruler_pattern_sha"] is None

    prov_ner = build_provenance(
        pass_type="ner",
        chunk_id="ch1",
        nlp=nlp,
        corpus_sha="a" * 64,
        pattern_sha="b" * 64,
    )
    assert prov_ner["entity_ruler_pattern_sha"] is None


# ---------------------------------------------------------------------------
# write_pass_artifact — atomic + deterministic
# ---------------------------------------------------------------------------


def _fake_response(pass_type: str = "noun_phrase", chunk_id: str = "ch1") -> ConceptDiscoveryResponse:
    return ConceptDiscoveryResponse(
        schema_version="1",
        pass_type=pass_type,  # type: ignore[arg-type]
        chunk_id=chunk_id,
        candidates=[
            ConceptCandidate(
                term="hearsay",
                canonical_form="hearsay",
                variants=[],
                example_quote="An out-of-court statement.",
            )
        ],
    )


def test_write_pass_artifact_creates_both_files(tmp_path: Path) -> None:
    resp = _fake_response()
    prov = {
        "pass_type": "noun_phrase",
        "chunk_id": "ch1",
        "frozen_timestamp": 0,
    }
    artifact_path, prov_path = write_pass_artifact(resp, tmp_path, prov)
    assert artifact_path == tmp_path / "noun_phrase_ch1.json"
    assert prov_path == tmp_path / "noun_phrase_ch1.provenance.json"
    assert artifact_path.is_file()
    assert prov_path.is_file()


def test_write_pass_artifact_is_atomic_and_deterministic(tmp_path: Path) -> None:
    """Two writes with identical inputs produce byte-identical files
    (orjson OPT_SORT_KEYS + OPT_INDENT_2)."""
    resp = _fake_response()
    prov = {
        "pass_type": "noun_phrase",
        "chunk_id": "ch1",
        "frozen_timestamp": 0,
    }
    d1 = tmp_path / "run1"
    d2 = tmp_path / "run2"
    d1.mkdir()
    d2.mkdir()
    a1, p1 = write_pass_artifact(resp, d1, prov)
    a2, p2 = write_pass_artifact(resp, d2, prov)
    assert a1.read_bytes() == a2.read_bytes()
    assert p1.read_bytes() == p2.read_bytes()


def test_write_pass_artifact_no_tmp_files_remain(tmp_path: Path) -> None:
    """The atomic tmp+rename pattern leaves no ``*.tmp`` debris behind."""
    resp = _fake_response()
    prov = {"pass_type": "noun_phrase", "chunk_id": "ch1", "frozen_timestamp": 0}
    write_pass_artifact(resp, tmp_path, prov)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


# ---------------------------------------------------------------------------
# run_all_symbolic — slow integration test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_run_all_symbolic_against_real_corpus(
    corpus_conn,  # session-scoped fixture from tests/unit/concepts/conftest.py
    nlp_with_doctrinal,
    tmp_path: Path,
) -> None:
    """Real corpus, chapter 1 only: 3 passes × 1 chapter = 3 results.

    Verifies:
        * 3 ``CallResult`` returned, all with ``error is None``.
        * 3 ``{pass_type}_ch1.json`` artifacts written.
        * 3 ``{pass_type}_ch1.provenance.json`` sidecars written.
    """
    corpus_path = REPO_ROOT / "artifacts" / "page_corpus.sqlite"
    patterns_path = REPO_ROOT / "fixtures" / "doctrinal_patterns.yaml"
    if not corpus_path.exists():
        pytest.skip(f"corpus not built: {corpus_path}")
    if not patterns_path.exists():
        pytest.skip(f"doctrinal patterns absent: {patterns_path}")

    results = run_all_symbolic(
        corpus_conn,
        nlp_with_doctrinal,
        tmp_path,
        chapters=(1,),
        corpus_path=corpus_path,
        doctrinal_patterns_path=patterns_path,
    )
    assert len(results) == 3
    failures = [r for r in results if r.error is not None]
    assert failures == [], failures
    # Every (pass × ch1) wrote its artifact + provenance pair.
    for pass_type in PASS_ORDER:
        assert (tmp_path / f"{pass_type}_ch1.json").is_file(), pass_type
        assert (tmp_path / f"{pass_type}_ch1.provenance.json").is_file(), pass_type
