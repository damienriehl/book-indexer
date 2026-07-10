"""``claude -p`` subprocess wrapper — the ONLY entry to the Claude CLI in src/.

Amended D-08 argv (03A-CONTEXT.md D-08 + 03A-RESEARCH.md §H-1):

- ``-p`` — non-interactive headless mode, required for ``--json-schema``.
- ``--output-format json`` — envelope-JSON on stdout (parsed via ``ClaudeCliEnvelope``).
- ``--json-schema <schema>`` — wire-level grammar-constrained decoding.
- ``--max-turns 8`` — broad-extraction passes (doctrinal, noun_phrase) need
  reasoning headroom; 2 was the H-1 minimum but produced ``error_max_turns``
  on the worst-case chapters even with v2 prompts (Plan 03A-09 retry #4).
- ``--allowed-tools ""`` — empty string, no tool use permitted.
- ``--model claude-sonnet-4-6`` — full name, NOT ``--fallback-model``.
- ``--effort medium`` — default; explicit for audit trail.
- ``--exclude-dynamic-system-prompt-sections`` — portable prompt-cache.
- ``--append-system-prompt <system_prompt_body>`` — CON-05 / CON-07 boilerplate.
- ``--no-session-persistence`` — no ~/.claude/sessions/ churn.

Forbidden flags:

- ``--bare`` — disables OAuth/keychain; forces ``ANTHROPIC_API_KEY`` billing (H-1).
- ``--temperature`` / ``--max-tokens`` — do not exist in ``claude 2.1.119`` (H-1).
- ``--fallback-model`` — defeats D-14 cache key's ``model_name`` factor.

H-8: ``structured_output`` is present-or-absent independently of ``is_error``;
parse the envelope then branch four-corner.

H-10: this module is the SINGLE entry to the CLI; Plan 03A-09's AST walker
(``test_no_anthropic_sdk_imports.py``) enforces no Anthropic-SDK imports.

requirements_addressed: CON-01
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import TypeVar

import orjson
from pydantic import BaseModel, ValidationError

from .errors import (
    SubagentAuthError,
    SubagentCliError,
    SubagentTimeoutError,
)
from .schema import (
    ClaudeCliEnvelope,
    ConceptDiscoveryResponse,
    build_json_schema,
)

# CUR-XX (Phase 7 Wave 1 additive refactor): TypeVar enables propose_*.py
# scripts to pass `response_cls=RemovalsResponse` / `RecapitalizationsResponse`
# while existing Phase 3a callers continue to default to ConceptDiscoveryResponse
# (zero behavior change for Phase 3a; argv-match invariant unchanged).
ResponseT = TypeVar("ResponseT", bound=BaseModel)

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "InvokeResult",
    "MAX_TURNS",
    "build_subprocess_args",
    "get_cli_version",
    "invoke_one",
    "preflight_auth_or_raise",
]


DEFAULT_MODEL = "claude-sonnet-4-6"
# Phase 3a 03A-09 retry decision history:
# - retry #2 (2026-04-25): 180s → 360s + workers 4 → 2 to reduce OAuth-refresh
#   contention (H-7). Eliminated most timeouts but still saw error_max_turns
#   on doctrinal/noun_phrase broad-extraction passes.
# - retry #4 (2026-04-23): 360s → 600s + MAX_TURNS 2 → 8. Broad-extraction
#   passes (doctrinal v2, noun_phrase v2) legitimately need >2 turns of
#   reasoning headroom on 50-page chapters even with the v2 no-preamble
#   protocol — the model writes the JSON object across multiple turns when
#   the candidate count approaches the 75-200 range. 8 turns is the
#   smallest power-of-two with sufficient slack; 600s wall-clock matches.
DEFAULT_TIMEOUT_S = 600
MAX_TURNS = "8"     # broad-extraction reasoning headroom (Plan 03A-09 retry #4)
EFFORT = "medium"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvokeResult:
    """One subprocess invocation + parsed response.

    Fields feed Plan 03A-04's ``provenance_dict`` + Plan 03A-07's call log.

    Phase 7 Wave 1 refactor: ``response`` is typed as ``BaseModel`` (was
    ``ConceptDiscoveryResponse``) so the same wrapper serves Phase 7 curator
    proposal scripts whose ``response_cls`` is ``RemovalsResponse`` or
    ``RecapitalizationsResponse``. Phase 3a callers continue to receive a
    ``ConceptDiscoveryResponse`` at runtime (default ``response_cls``); the
    type widening is additive and does not break any existing call site.
    """

    response: BaseModel
    cli_version: str
    duration_ms: int
    subprocess_args: list[str]
    stderr_tail: str


# ---------------------------------------------------------------------------
# Env sanitization
# ---------------------------------------------------------------------------


def _sanitize_env() -> dict[str, str]:
    """Build the child env for ``subprocess.run(env=...)``.

    Keep:  HOME (OAuth keychain; USERPROFILE/HOMEDRIVE/HOMEPATH are the Windows
           equivalents), PATH (locate ``claude``), XDG_* (for keychain),
           Windows runtime vars (APPDATA/LOCALAPPDATA/SYSTEMROOT/PATHEXT/TEMP/TMP
           — required for a child process to spawn at all on Windows),
           determinism vars (TZ, LC_ALL, PYTHONHASHSEED).
    Force: TZ=UTC, LC_ALL=C.UTF-8, PYTHONHASHSEED=0 (if unset).
    Drop:  CLAUDE_CODE_* (tool runtime vars that could perturb output),
           ANTHROPIC_API_KEY (prevents accidental API-billing path under --bare,
           which we don't use, but defensive).
    """
    src = os.environ
    keep_keys = (
        "HOME", "PATH", "USER", "LOGNAME",
        # Windows equivalents / requirements.
        "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        "APPDATA", "LOCALAPPDATA", "SYSTEMROOT", "PATHEXT", "TEMP", "TMP",
    )
    xdg_keys = tuple(k for k in src if k.startswith("XDG_"))
    env: dict[str, str] = {}
    for k in keep_keys + xdg_keys:
        if k in src:
            env[k] = src[k]
    # Determinism.
    env["TZ"] = "UTC"
    env["LC_ALL"] = "C.UTF-8"
    env.setdefault("PYTHONHASHSEED", "0")
    # Explicit drops.
    for k in ("ANTHROPIC_API_KEY",) + tuple(k for k in src if k.startswith("CLAUDE_CODE_")):
        env.pop(k, None)
    return env


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def preflight_auth_or_raise() -> None:
    """Run ``claude auth status``; raise ``SubagentAuthError`` if not logged-in.

    Call ONCE at pipeline startup (Plan 03A-08 CLI entry). Prevents 20 cryptic
    subprocess failures when the user has silently lost auth.
    """
    try:
        r = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=15, check=False,
            env=_sanitize_env(),
        )
    except FileNotFoundError as exc:
        raise SubagentAuthError(
            "`claude` CLI not found on PATH. Install Claude Code and ensure the "
            "`claude` binary is reachable."
        ) from exc
    raw = r.stdout + r.stderr
    out = raw.lower()
    # ``claude auth status`` has two known output formats:
    #
    #   (a) Older textual form: "Logged in as <email>" / "Not logged in".
    #       The naive ``"logged in" not in out`` check fails because
    #       "Not logged in" CONTAINS "logged in" — we therefore look for
    #       the explicit negative marker.
    #
    #   (b) Newer JSON form (claude 2.1.119+ on 2026-04+): emits a JSON
    #       object containing ``"loggedIn": true`` / ``"loggedIn": false``.
    #       After lowercasing, the substring is ``"loggedin": true`` (the
    #       space inside ``logged in`` is gone), so neither (a) marker
    #       fires — without explicit JSON handling we'd false-fail on a
    #       valid logged-in CLI.
    #
    # We try strict JSON parse first (most reliable), then fall back to
    # substring heuristics for textual output.
    json_logged_in: bool | None = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and "loggedIn" in parsed:
            json_logged_in = bool(parsed["loggedIn"])
    except (json.JSONDecodeError, ValueError):
        json_logged_in = None
    if json_logged_in is None:
        # Substring heuristics over lowercased output. Also catch the
        # space-collapsed JSON form ``"loggedin": true|false`` defensively
        # (in case future Claude CLI tweaks whitespace inside the JSON).
        has_negative_marker = (
            "not logged in" in out
            or '"loggedin": false' in out
            or '"loggedin":false' in out
        )
        has_logged_in_marker = (
            ("logged in" in out and "not logged in" not in out)
            or '"loggedin": true' in out
            or '"loggedin":true' in out
        )
        is_logged_out = (
            has_negative_marker
            or r.returncode != 0
            or not has_logged_in_marker
        )
    else:
        is_logged_out = (not json_logged_in) or r.returncode != 0
    if is_logged_out:
        raise SubagentAuthError(
            "claude auth status reports not-logged-in. Run `claude /login` "
            "(Claude Max OAuth) and retry. Do NOT set ANTHROPIC_API_KEY — this "
            "pipeline is a Claude Max subscription user (CON-01).\n"
            f"Observed:\n  stdout: {r.stdout!s}\n  stderr: {r.stderr!s}"
        )


def get_cli_version() -> str:
    """Return ``claude --version`` output trimmed of trailing whitespace."""
    r = subprocess.run(
        ["claude", "--version"],
        capture_output=True, text=True, timeout=10, check=False,
        env=_sanitize_env(),
    )
    return (r.stdout or "").strip() or "unknown"


# ---------------------------------------------------------------------------
# Argv builder
# ---------------------------------------------------------------------------


def build_subprocess_args(
    *,
    system_prompt_body: str,
    model_name: str = DEFAULT_MODEL,
    schema_json: str | None = None,
) -> list[str]:
    """Return the exact argv for ``invoke_one``.

    Exposed as a pure function so Plan 03A-09's argv-match test can lock the
    flag set without running a real subprocess.
    """
    if schema_json is None:
        schema_json = json.dumps(build_json_schema(), sort_keys=True)
    return [
        "claude",
        "-p",
        "--output-format", "json",
        "--json-schema", schema_json,
        "--max-turns", MAX_TURNS,
        "--allowed-tools", "",
        "--model", model_name,
        "--effort", EFFORT,
        "--exclude-dynamic-system-prompt-sections",
        "--append-system-prompt", system_prompt_body,
        "--no-session-persistence",
    ]


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def invoke_one(
    *,
    chunk_id: str,
    pass_type: str,
    prompt_body: str,
    system_prompt_body: str,
    model_name: str = DEFAULT_MODEL,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    response_cls: type[BaseModel] = ConceptDiscoveryResponse,
    schema_json: str | None = None,
) -> InvokeResult:
    """Run one ``claude -p`` call and parse the response.

    Args:
        chunk_id: e.g. ``"ch2"`` — used in exception context for debugging.
        pass_type: one of ``"noun_phrase" | "doctrinal" | "ner" | "implicit"``.
        prompt_body: the stdin payload — per-pass prompt + chunk_text.
        system_prompt_body: content of ``prompts/system_prompt_v{N}.md``,
            fed via ``--append-system-prompt``.
        model_name: full model identifier (part of the D-14 cache key).
        timeout_s: kills the child on hung CLI.

    Returns:
        ``InvokeResult`` on success — response validated against
        ``ConceptDiscoveryResponse``, CLI version + duration captured.

    Raises:
        SubagentTimeoutError: subprocess timed out.
        SubagentCliError: envelope ``is_error=True`` OR ``structured_output is None``
            OR the parsed response fails Pydantic validation. H-8 four-corner
            handling all collapse to this typed exception.
        SubagentAuthError: **NOT raised from here** — call ``preflight_auth_or_raise``
            at pipeline startup instead. If auth dies mid-run it surfaces as a
            ``SubagentCliError`` envelope with is_error=True.
    """
    # If caller passed a non-default response_cls, derive its schema; otherwise
    # build_subprocess_args defaults to ConceptDiscoveryResponse's schema (Phase
    # 3a behavior — argv-match invariant unchanged).
    if schema_json is None and response_cls is not ConceptDiscoveryResponse:
        schema_json = json.dumps(response_cls.model_json_schema(), sort_keys=True)
    argv = build_subprocess_args(
        system_prompt_body=system_prompt_body,
        model_name=model_name,
        schema_json=schema_json,
    )
    try:
        r = subprocess.run(
            argv,
            input=prompt_body,
            capture_output=True, check=False, text=True,
            timeout=timeout_s,
            env=_sanitize_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise SubagentTimeoutError(
            f"claude -p timed out after {timeout_s}s for "
            f"(chunk_id={chunk_id!r}, pass_type={pass_type!r})"
        ) from exc

    stderr_tail = (r.stderr or "")[-2000:]

    # Parse envelope. If stdout isn't JSON, that itself is a CliError.
    if not r.stdout:
        raise SubagentCliError(
            f"empty stdout from claude -p (chunk_id={chunk_id!r}, "
            f"pass_type={pass_type!r}, returncode={r.returncode}, "
            f"stderr_tail={stderr_tail!r})"
        )
    try:
        raw = orjson.loads(r.stdout)
    except orjson.JSONDecodeError as exc:
        raise SubagentCliError(
            f"claude -p stdout is not valid JSON (chunk_id={chunk_id!r}, "
            f"pass_type={pass_type!r}): {exc}"
        ) from exc

    try:
        envelope = ClaudeCliEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise SubagentCliError(
            f"claude -p envelope shape unexpected (chunk_id={chunk_id!r}, "
            f"pass_type={pass_type!r}): {exc}"
        ) from exc

    # H-8 four-corner: branch on envelope fields INDEPENDENTLY.
    if envelope.is_error:
        raise SubagentCliError(
            f"claude -p reported is_error=True (chunk_id={chunk_id!r}, "
            f"pass_type={pass_type!r}, subtype={envelope.subtype!r}, "
            f"stderr_tail={stderr_tail!r})"
        )
    if envelope.structured_output is None:
        raise SubagentCliError(
            f"claude -p succeeded but emitted no structured_output "
            f"(chunk_id={chunk_id!r}, pass_type={pass_type!r}, "
            f"subtype={envelope.subtype!r}, result={envelope.result!r}). "
            f"Model may have refused; consider bumping --max-turns or "
            f"tightening the prompt."
        )

    # Final gate: Pydantic validation of the actual response payload.
    try:
        response = response_cls.model_validate(envelope.structured_output)
    except ValidationError as exc:
        raise SubagentCliError(
            f"claude -p structured_output failed {response_cls.__name__} "
            f"validation (chunk_id={chunk_id!r}, pass_type={pass_type!r}): {exc}"
        ) from exc

    # Cross-check: envelope's chunk_id / pass_type should match the caller.
    # Only applies to ConceptDiscoveryResponse (Phase 3a) — Phase 7 curator
    # responses (RemovalsResponse, RecapitalizationsResponse) do not carry
    # chunk_id/pass_type fields, so skip the drift check for them.
    if response_cls is ConceptDiscoveryResponse:
        if response.chunk_id != chunk_id:  # type: ignore[attr-defined]
            raise SubagentCliError(
                f"chunk_id drift: requested {chunk_id!r}, response says "
                f"{response.chunk_id!r}"  # type: ignore[attr-defined]
            )
        if response.pass_type != pass_type:  # type: ignore[attr-defined]
            raise SubagentCliError(
                f"pass_type drift: requested {pass_type!r}, response says "
                f"{response.pass_type!r}"  # type: ignore[attr-defined]
            )

    duration_ms = int(envelope.duration_ms or 0)

    return InvokeResult(
        response=response,
        cli_version=get_cli_version(),
        duration_ms=duration_ms,
        subprocess_args=list(argv),
        stderr_tail=stderr_tail,
    )
