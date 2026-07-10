"""PDF loader — wraps pymupdf.open with context-manager semantics."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pymupdf


class PdfLoader:
    """Context manager for pymupdf.Document lifecycle.

    Usage:
        with PdfLoader.open_document(path) as doc:
            ...
    """

    @staticmethod
    @contextmanager
    def open_document(path: str | Path) -> Iterator[pymupdf.Document]:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"PDF not found: {p}")
        doc = pymupdf.open(str(p))
        try:
            yield doc
        finally:
            doc.close()
