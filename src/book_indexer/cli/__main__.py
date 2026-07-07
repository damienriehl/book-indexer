"""``python -m book_indexer.cli`` entry point.

Calls into typer's Typer() instance defined in ``app.py``. Typer's ``app()``
handles argparse-equivalent SystemExit semantics; we don't wrap.

The ``[project.scripts] index-book`` entry point in ``pyproject.toml`` resolves
to ``book_indexer.cli.__main__:app`` directly — installing via
``uv tool install .`` or ``uv pip install -e .`` registers ``index-book`` on
PATH (CONTEXT 06 D-03; RESEARCH §H-1).
"""
from __future__ import annotations

from .app import app

if __name__ == "__main__":  # pragma: no cover
    # Force prog_name="index-book" so `python -m book_indexer.cli --help`
    # produces byte-identical Usage line to `index-book --help` (Lock #5 /
    # OUT-04 cross-invocation byte-identity per Plan 06-00 acceptance #4).
    app(prog_name="index-book")
