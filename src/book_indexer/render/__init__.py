"""Phase 5 rendering & audit-artifact emission.

Public API: re-exported IR (IndexTree/IndexEntry/SubEntry/Locator)
from upstream phases plus two Phase-5-owned dataclasses (SyntheticEntry,
FormattedLocator) and the AUD-04 Metadata Pydantic model. Locator is
the single source of truth from book_indexer.tables.ir per D-07.

Wave 1-3 modules (filter, synthesize, range_collapse, markdown, docx,
audit, coverage) are imported lazily by __main__.py to keep test
fixtures fast (no spaCy or python-docx load on import).
"""
from book_indexer.assembly import IndexEntry, IndexTree, IndexTreeProvenance, SubEntry
from book_indexer.tables.ir import Locator

from .errors import FreezeError, MetadataValidationError, RenderError
from .ir import FormattedLocator, SyntheticEntry
from .metadata import Metadata, build_metadata

__all__ = [
    "FormattedLocator",
    "FreezeError",
    "IndexEntry",
    "IndexTree",
    "IndexTreeProvenance",
    "Locator",
    "Metadata",
    "MetadataValidationError",
    "RenderError",
    "SubEntry",
    "SyntheticEntry",
    "build_metadata",
]
