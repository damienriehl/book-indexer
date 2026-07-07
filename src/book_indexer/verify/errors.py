"""Phase 2 typed errors. Keep small; each error is a CI ship-blocker when raised."""
from __future__ import annotations


class VerifierError(RuntimeError):
    """Unrecoverable error during ``verify()`` composition.

    Raised when the matcher / section_path / snippet sub-layers produce state
    that cannot yield a valid Evidence (e.g., cycle in sections.parent_id,
    section_path walk exceeded 5 hops, snippet could not reach 60 chars).
    Subclass of RuntimeError because these are internal consistency failures,
    not input-validation failures.
    """


class EvidenceValidationError(ValueError):
    """VER-04: an Evidence instance failed its Pydantic model validation.

    Raised when the matcher emits a hit whose section_path / section_level /
    verbatim_snippet / section_ref do not satisfy the Evidence schema. This
    indicates matcher-versus-schema drift; the ship-blocker test
    ``tests/invariants/test_evidence_ledger.py`` will also surface this.
    """
