"""Enable ``python -m book_indexer.ingest <pdf>`` invocation.

Delegates to :mod:`book_indexer.cli.ingest_main` so the CLI code lives in
one place; this module exists purely to make the ``-m`` syntax work.
"""
from __future__ import annotations

import sys

from book_indexer.cli.ingest_main import main

if __name__ == "__main__":
    sys.exit(main())
