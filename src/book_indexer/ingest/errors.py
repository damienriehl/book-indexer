"""Phase 1 typed errors. Keep small; each error is a CI ship-blocker when raised."""
from __future__ import annotations


class FolioMonotonicityError(ValueError):
    """D-05, FOL-05: folio decreased within a folio_style section."""


class FolioContiguityError(ValueError):
    """Implausible gap between adjacent resolved folios (>2 PDF pages of gap)."""


class FolioResolutionError(RuntimeError):
    """Unrecoverable error during the 4-tier folio cascade resolution."""


class FolioFixtureMismatchError(AssertionError):
    """Resolver output disagrees with fixtures/folios.yaml (D-04, FOL-04)."""


class DeterminismViolationError(RuntimeError):
    """Two-runs-diff check failed; corpus is not byte-identical across runs."""


class SectionMonotonicityError(ValueError):
    """D-26, SEC-08: Section numbering is not strictly monotonic within scope.

    Chapter-scoped: within each ``§ N`` chapter-section, level-2 ``N.NN`` values
    must strictly increase; within each level-2 ``N.NN``, level-3 ``.M`` values
    must strictly increase. The resolver never silently fixes the violation —
    the build halts so the human can inspect the PDF.
    """


class SectionFixtureMismatchError(AssertionError):
    """D-27, SEC-07: resolver output disagrees with ``fixtures/sections.yaml``
    on any entry (``ref``, ``level``, ``start_pdf_page``, or ``title``).

    The fixture is the CONTRACT — build fails, no soft-fail path.
    """
