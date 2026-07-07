"""Phase 3a typed errors. Keep small; each error is a CI ship-blocker when raised."""
from __future__ import annotations


class ConceptDiscoveryError(RuntimeError):
    """CON-01: unrecoverable error during concept-discovery pipeline composition.

    Base class for all Phase 3a failures. Subclass of RuntimeError because these
    are internal-consistency failures (subprocess/CLI/cache), not user-input
    validation failures (those raise ``pydantic.ValidationError`` directly from
    ``ConceptCandidate`` / ``ConceptDiscoveryResponse``).
    """


class SubagentTimeoutError(ConceptDiscoveryError):
    """CON-01: ``subprocess.run(timeout=180)`` fired before the CLI returned.

    Wraps ``subprocess.TimeoutExpired``; recorded in the provenance sidecar
    as ``miss_reason="timeout"`` (D-15). Coverage report surfaces the miss.
    """


class SubagentAuthError(ConceptDiscoveryError):
    """CON-01: ``claude auth status`` reports logged-out at pipeline startup.

    Raised BEFORE any subprocess cycle so 20 cryptic subprocess failures are
    avoided (03A-CONTEXT.md D-08 auth smoke-test). Actionable message:
    ``Please run 'claude /login' or set a long-lived token via 'claude setup-token'``.
    """


class SubagentCliError(ConceptDiscoveryError):
    """CON-01: ``claude -p`` returned non-zero exit OR envelope ``is_error=True``
    OR envelope lacks ``structured_output`` after ``--max-turns`` exhaustion.

    H-8: do NOT assume ``is_error=False`` implies ``structured_output is not None``;
    the envelope parser checks both independently.
    """


class SchemaRetryExhaustedError(ConceptDiscoveryError):
    """CON-02, CON-07: schema-repair retries exhausted (planner discretion item —
    max 2 retries appending the ``ValidationError`` message to the prompt; on
    the third failure, emit empty candidates and log the miss).
    """


class PromptFileNotFoundError(FileNotFoundError):
    """D-10, D-17: ``concept_discovery_<pass>_v{N}.md`` missing or
    ``system_prompt_v{N}.md`` missing.

    Subclass of FileNotFoundError so callers can ``except FileNotFoundError``
    generically; the typed subclass names the contract for audit.
    """


class CacheKeyDriftError(AssertionError):
    """CON-06, D-14: reading a cache file whose recomputed sha does not match
    its filename.

    Defensive; not expected in practice but catches hand-edited cache entries
    before they pollute the union pool. AssertionError subclass so CI treats
    it as a build-time failure.
    """
