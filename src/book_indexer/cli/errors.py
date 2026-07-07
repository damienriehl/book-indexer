"""CLI-specific exceptions (``CLIError`` base + 3 subclasses)."""
from __future__ import annotations


class CLIError(Exception):
    """Base for all CLI-tier errors."""


class PdfShaMismatchError(CLIError):
    """Input PDF SHA-256 doesn't match the committed artifacts (CLI-03)."""


class VerifyAgainstDriftError(CLIError):
    """``--verify-against`` detected entry-level drift exceeding ``--allow-drift`` (D-04)."""


class RebuildDriftError(CLIError):
    """A ``--rebuild-*`` flag produced output that doesn't match committed artifacts."""
