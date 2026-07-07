"""Unit tests for ``src/book_indexer/concepts/subagent.py``.

All ``subprocess.run`` calls are monkey-patched — no live CLI invocation.
(Live CLI coverage lives in the Plan 03A-09 integration / cache-hit-rate tests.)

requirements_addressed: CON-01
"""
from __future__ import annotations

import json
import subprocess
from typing import Any

import orjson
import pytest

from book_indexer.concepts.errors import (
    SubagentAuthError,
    SubagentCliError,
    SubagentTimeoutError,
)
from book_indexer.concepts.subagent import (
    DEFAULT_MODEL,
    MAX_TURNS,
    _sanitize_env,
    build_subprocess_args,
    get_cli_version,
    invoke_one,
    preflight_auth_or_raise,
)

# ---------------------------------------------------------------------------
# Argv
# ---------------------------------------------------------------------------


def test_argv_has_mandatory_flags_only() -> None:
    argv = build_subprocess_args(system_prompt_body="sys prompt")
    # Every mandatory flag present.
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--output-format" in argv and argv[argv.index("--output-format") + 1] == "json"
    assert "--json-schema" in argv
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == MAX_TURNS == "8"
    assert "--allowed-tools" in argv and argv[argv.index("--allowed-tools") + 1] == ""
    assert "--model" in argv and argv[argv.index("--model") + 1] == DEFAULT_MODEL
    assert "--effort" in argv and argv[argv.index("--effort") + 1] == "medium"
    assert "--exclude-dynamic-system-prompt-sections" in argv
    assert "--append-system-prompt" in argv
    assert argv[argv.index("--append-system-prompt") + 1] == "sys prompt"
    assert "--no-session-persistence" in argv


def test_argv_has_no_forbidden_flags() -> None:
    """H-1: none of --bare / --temperature / --max-tokens / --fallback-model."""
    argv = build_subprocess_args(system_prompt_body="sys")
    assert "--bare" not in argv
    assert "--temperature" not in argv
    assert "--max-tokens" not in argv
    assert "--fallback-model" not in argv


def test_argv_json_schema_is_sorted_json() -> None:
    """Schema is emitted via json.dumps(..., sort_keys=True) for byte-stability."""
    argv = build_subprocess_args(system_prompt_body="sys")
    schema_str = argv[argv.index("--json-schema") + 1]
    schema = json.loads(schema_str)
    # Re-serialize with the same sort_keys=True; the two strings must match.
    assert json.dumps(schema, sort_keys=True) == schema_str


# ---------------------------------------------------------------------------
# _sanitize_env
# ---------------------------------------------------------------------------


def test_sanitize_env_forces_determinism_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTHONHASHSEED", raising=False)
    env = _sanitize_env()
    assert env["TZ"] == "UTC"
    assert env["LC_ALL"] == "C.UTF-8"
    assert env["PYTHONHASHSEED"] == "0"


def test_sanitize_env_drops_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-rogue")
    env = _sanitize_env()
    assert "ANTHROPIC_API_KEY" not in env


def test_sanitize_env_drops_claude_code_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_FOO", "bar")
    monkeypatch.setenv("CLAUDE_CODE_BAZ", "qux")
    env = _sanitize_env()
    assert not any(k.startswith("CLAUDE_CODE_") for k in env)


def test_sanitize_env_preserves_path_and_home(monkeypatch: pytest.MonkeyPatch) -> None:
    env = _sanitize_env()
    # PATH is critical for locating ``claude`` on the child process.
    assert "PATH" in env
    # HOME is critical for OAuth keychain reads.
    assert "HOME" in env


# ---------------------------------------------------------------------------
# invoke_one — mocked subprocess
# ---------------------------------------------------------------------------


def _valid_envelope_stdout() -> str:
    """A well-formed claude -p envelope with a minimal ConceptDiscoveryResponse."""
    return orjson.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "",
        "structured_output": {
            "schema_version": "1",
            "pass_type": "doctrinal",
            "chunk_id": "ch1",
            "candidates": [{
                "term": "hearsay",
                "canonical_form": "hearsay",
                "variants": [],
                "example_quote": "an out-of-court statement",
            }],
        },
        "stop_reason": "end_turn",
        "duration_ms": 9823,
        "num_turns": 2,
    }).decode("utf-8")


class _FakeCompleted:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _install_subprocess_mock(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str | None = None,
    stderr: str = "",
    returncode: int = 0,
    raises: Exception | None = None,
    cli_version_stdout: str = "claude 2.1.119",
) -> list[dict[str, Any]]:
    """Patch subagent.subprocess.run; return a list that records every call."""
    calls: list[dict[str, Any]] = []

    def fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        calls.append({"argv": list(argv), "kwargs": kw})
        if raises is not None:
            raise raises
        # Differentiate ``claude --version`` from actual ``claude -p`` calls.
        if argv[:2] == ["claude", "--version"]:
            return _FakeCompleted(stdout=cli_version_stdout, stderr="", returncode=0)
        if argv[:3] == ["claude", "auth", "status"]:
            return _FakeCompleted(stdout="Logged in as user@example.com", stderr="", returncode=0)
        assert stdout is not None, "test must supply stdout for claude -p invocations"
        return _FakeCompleted(stdout=stdout, stderr=stderr, returncode=returncode)

    import book_indexer.concepts.subagent as subagent_mod
    monkeypatch.setattr(subagent_mod.subprocess, "run", fake_run)
    return calls


def test_invoke_one_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_subprocess_mock(monkeypatch, stdout=_valid_envelope_stdout())
    result = invoke_one(
        chunk_id="ch1", pass_type="doctrinal",
        prompt_body="please extract",
        system_prompt_body="no locators",
    )
    assert result.response.chunk_id == "ch1"
    assert result.response.pass_type == "doctrinal"
    assert len(result.response.candidates) == 1
    assert result.duration_ms == 9823
    assert result.cli_version == "claude 2.1.119"
    # At least one call to claude -p was made.
    main_calls = [c for c in calls if c["argv"][:2] == ["claude", "-p"]]
    assert len(main_calls) == 1


def test_invoke_one_stdin_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """H-1 / D-08: prompt body MUST be delivered via input=, not positional."""
    calls = _install_subprocess_mock(monkeypatch, stdout=_valid_envelope_stdout())
    invoke_one(
        chunk_id="ch1", pass_type="doctrinal",
        prompt_body="this is the prompt body",
        system_prompt_body="sys",
    )
    main_call = [c for c in calls if c["argv"][:2] == ["claude", "-p"]][0]
    # The prompt body appears as the `input=` kwarg, NOT as an argv element.
    assert main_call["kwargs"].get("input") == "this is the prompt body"
    # Defensive: the prompt body is NOT present in argv.
    assert "this is the prompt body" not in main_call["argv"]


def test_invoke_one_timeout_raises_SubagentTimeoutError(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_subprocess_mock(
        monkeypatch,
        raises=subprocess.TimeoutExpired(cmd=["claude"], timeout=600),
    )
    with pytest.raises(SubagentTimeoutError) as exc:
        invoke_one(
            chunk_id="ch1", pass_type="doctrinal",
            prompt_body="x", system_prompt_body="sys",
        )
    assert "ch1" in str(exc.value)
    assert "doctrinal" in str(exc.value)


def test_invoke_one_is_error_true_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = orjson.dumps({
        "type": "result", "subtype": "error_max_turns",
        "is_error": True, "result": "", "structured_output": None,
        "duration_ms": 1200,
    }).decode("utf-8")
    _install_subprocess_mock(monkeypatch, stdout=bad)
    with pytest.raises(SubagentCliError) as exc:
        invoke_one(
            chunk_id="ch1", pass_type="doctrinal",
            prompt_body="x", system_prompt_body="sys",
        )
    assert "is_error" in str(exc.value) or "error_max_turns" in str(exc.value)


def test_invoke_one_structured_output_none_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H-8: is_error=False AND structured_output=None is still an error for us."""
    bad = orjson.dumps({
        "type": "result", "subtype": "success",
        "is_error": False, "result": "model refused",
        "structured_output": None, "duration_ms": 1200,
    }).decode("utf-8")
    _install_subprocess_mock(monkeypatch, stdout=bad)
    with pytest.raises(SubagentCliError) as exc:
        invoke_one(
            chunk_id="ch1", pass_type="doctrinal",
            prompt_body="x", system_prompt_body="sys",
        )
    assert "structured_output" in str(exc.value)


def test_invoke_one_structured_output_fails_concept_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = orjson.dumps({
        "type": "result", "subtype": "success",
        "is_error": False, "result": "",
        "structured_output": {
            "schema_version": "1", "pass_type": "doctrinal", "chunk_id": "ch1",
            "candidates": [{"term": "hearsay", "canonical_form": "hearsay",
                             "variants": [], "example_quote": "ok",
                             "page": 87}],   # extra field → ValidationError
        },
    }).decode("utf-8")
    _install_subprocess_mock(monkeypatch, stdout=bad)
    with pytest.raises(SubagentCliError) as exc:
        invoke_one(
            chunk_id="ch1", pass_type="doctrinal",
            prompt_body="x", system_prompt_body="sys",
        )
    assert "ConceptDiscoveryResponse" in str(exc.value) or "page" in str(exc.value)


def test_invoke_one_chunk_id_drift_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = orjson.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": "",
        "structured_output": {
            "schema_version": "1", "pass_type": "doctrinal",
            "chunk_id": "ch9",    # requested ch1
            "candidates": [],
        },
    }).decode("utf-8")
    _install_subprocess_mock(monkeypatch, stdout=bad)
    with pytest.raises(SubagentCliError, match="chunk_id"):
        invoke_one(chunk_id="ch1", pass_type="doctrinal", prompt_body="x", system_prompt_body="sys")


def test_invoke_one_pass_type_drift_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = orjson.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": "",
        "structured_output": {
            "schema_version": "1", "pass_type": "ner",     # requested doctrinal
            "chunk_id": "ch1", "candidates": [],
        },
    }).decode("utf-8")
    _install_subprocess_mock(monkeypatch, stdout=bad)
    with pytest.raises(SubagentCliError, match="pass_type"):
        invoke_one(chunk_id="ch1", pass_type="doctrinal", prompt_body="x", system_prompt_body="sys")


def test_invoke_one_malformed_json_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_subprocess_mock(monkeypatch, stdout="not-json-at-all")
    with pytest.raises(SubagentCliError):
        invoke_one(chunk_id="ch1", pass_type="doctrinal", prompt_body="x", system_prompt_body="sys")


def test_invoke_one_empty_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_subprocess_mock(monkeypatch, stdout="")
    with pytest.raises(SubagentCliError, match="empty stdout"):
        invoke_one(chunk_id="ch1", pass_type="doctrinal", prompt_body="x", system_prompt_body="sys")


# ---------------------------------------------------------------------------
# preflight_auth_or_raise
# ---------------------------------------------------------------------------


def test_preflight_auth_ok_when_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_subprocess_mock(monkeypatch, stdout="")  # claude -p is unused here
    preflight_auth_or_raise()  # must not raise


def test_preflight_auth_raises_when_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        if argv[:3] == ["claude", "auth", "status"]:
            return _FakeCompleted(stdout="Not logged in · Please run /login", stderr="")
        return _FakeCompleted(stdout="")

    import book_indexer.concepts.subagent as subagent_mod
    monkeypatch.setattr(subagent_mod.subprocess, "run", fake_run)
    with pytest.raises(SubagentAuthError):
        preflight_auth_or_raise()


def test_preflight_auth_raises_when_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        raise FileNotFoundError(2, "No such file", "claude")

    import book_indexer.concepts.subagent as subagent_mod
    monkeypatch.setattr(subagent_mod.subprocess, "run", fake_run)
    with pytest.raises(SubagentAuthError, match="not found"):
        preflight_auth_or_raise()


def test_preflight_auth_ok_with_json_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """``claude auth status`` JSON form (claude 2.1.119+) must be accepted.

    Rule-1 fix verifier: the original parser only matched textual ``Logged in``.
    On a JSON-emitting CLI the lowercased substring becomes ``"loggedin": true``
    (no space), so without explicit JSON handling the preflight false-fails on a
    perfectly-authenticated CLI.
    """
    json_blob = (
        '{\n'
        '  "loggedIn": true,\n'
        '  "authMethod": "claude.ai",\n'
        '  "subscriptionType": "max"\n'
        '}\n'
    )

    def fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        if argv[:3] == ["claude", "auth", "status"]:
            return _FakeCompleted(stdout=json_blob, stderr="", returncode=0)
        return _FakeCompleted(stdout="")

    import book_indexer.concepts.subagent as subagent_mod
    monkeypatch.setattr(subagent_mod.subprocess, "run", fake_run)
    preflight_auth_or_raise()  # must not raise


def test_preflight_auth_raises_with_json_logged_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON form with ``"loggedIn": false`` must raise ``SubagentAuthError``."""
    json_blob = '{"loggedIn": false, "authMethod": null}\n'

    def fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        if argv[:3] == ["claude", "auth", "status"]:
            return _FakeCompleted(stdout=json_blob, stderr="", returncode=0)
        return _FakeCompleted(stdout="")

    import book_indexer.concepts.subagent as subagent_mod
    monkeypatch.setattr(subagent_mod.subprocess, "run", fake_run)
    with pytest.raises(SubagentAuthError):
        preflight_auth_or_raise()


def test_get_cli_version_returns_stripped_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, *a, **kw):  # type: ignore[no-untyped-def]
        return _FakeCompleted(stdout="  claude 2.1.119\n", stderr="")

    import book_indexer.concepts.subagent as subagent_mod
    monkeypatch.setattr(subagent_mod.subprocess, "run", fake_run)
    assert get_cli_version() == "claude 2.1.119"
