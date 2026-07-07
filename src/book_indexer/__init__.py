"""book-indexer — a deterministic back-of-book indexer with symbolic
page-citation verification.

Core guarantee: ``verify(term, folio, conn) -> Evidence | None`` is the *sole*
code path that emits a page citation. The LLM (used only for optional concept
discovery) never assigns a page number. This invariant is enforced by a
static-analysis test (``tests/invariants/test_verify_is_sole_locator_source.py``).

The public API is exposed lazily (PEP 562) so ``import book_indexer`` stays
instant and does not eagerly import PyMuPDF or spaCy.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from book_indexer.config import BookConfig, load_book_config

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "BookConfig",
    "load_book_config",
    "Evidence",
    "verify",
    "run_ingest",
    "build_index_tree",
    "render_markdown",
]

# name -> (submodule, attribute)
_LAZY: dict[str, tuple[str, str]] = {
    "Evidence": ("book_indexer.verify.evidence", "Evidence"),
    "verify": ("book_indexer.verify.verifier", "verify"),
    "run_ingest": ("book_indexer.ingest.pipeline", "run_ingest"),
    "build_index_tree": ("book_indexer.assembly.tree", "build_index_tree"),
    "render_markdown": ("book_indexer.render.markdown", "render_markdown"),
}

if TYPE_CHECKING:  # pragma: no cover - typing only
    from book_indexer.assembly.tree import build_index_tree
    from book_indexer.ingest.pipeline import run_ingest
    from book_indexer.render.markdown import render_markdown
    from book_indexer.verify.evidence import Evidence
    from book_indexer.verify.verifier import verify


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'book_indexer' has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0])
    return getattr(mod, target[1])


def __dir__() -> list[str]:
    return sorted(__all__)
