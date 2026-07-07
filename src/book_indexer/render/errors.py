"""Typed exceptions for Phase 5 render stages.

Each exception corresponds to a build-failure scenario in
RESEARCH §H-13 (Failure-Mode Handling).
"""
from __future__ import annotations


class RenderError(Exception):
    """Base for all render errors."""


class FreezeError(RenderError):
    """Raised by docx.freeze_docx() if zip rewrite fails.

    Causes: source .docx corrupted, missing core.xml, regex normalize
    failed to match dcterms timestamps. Lock #5 is non-negotiable so
    this propagates without fallback.
    """


class MetadataValidationError(RenderError):
    """Raised by metadata.build_metadata() if a required version pin
    can't be located (provenance file missing or malformed).

    Lock #2 (Pydantic frozen+forbid) catches schema drift; this catches
    SOURCE drift (e.g., index_tree.provenance.json renamed a field).
    """
