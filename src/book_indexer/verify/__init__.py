"""Public API of the verifier subpackage.

``verify()`` is the sole locator-emitting function in the project
(Architecture Lock #1). Downstream consumers (Phase 3b citation tables,
Phase 4 assembly) import ONLY this package — never ``verifier.py`` directly.
This surface is what Plan 02-04's AST static-analysis test protects.
"""
from .errors import EvidenceValidationError, VerifierError
from .evidence import Evidence
from .verifier import verify

__all__ = ["Evidence", "EvidenceValidationError", "VerifierError", "verify"]
