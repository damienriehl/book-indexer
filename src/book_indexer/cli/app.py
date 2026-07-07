"""typer.Typer() instance + flat subcommand registration.

CRITICAL determinism settings (mitigate RESEARCH §H-13 Pitfall 1):

* ``pretty_exceptions_enable=False`` — disable Rich tracebacks (ANSI in CI logs).
* ``add_completion=False`` — drop locale-dependent completion subcommand.
* ``no_args_is_help=True`` — bare ``index-book`` prints help and exits 0.

Subcommand registration is flat (no ``add_typer`` / sub-app composition). Mirrors
sibling ``__main__.py`` argparse shapes (``assembly/``, ``tables/``, ``render/``).

Plan 06-00 (this plan) ships all 4 subcommand bodies as stubs that print
``"Not yet implemented"`` and return 0 — keeping ``index-book --help``
byte-deterministic for OUT-04 / QUAL-01 verification before Plan 06-01 fills
bodies.
"""
from __future__ import annotations

from pathlib import Path

import typer

from .subcommands import build as _build_mod
from .subcommands import replay as _replay_mod
from .subcommands import review as _review_mod
from .subcommands import verify as _verify_mod

app = typer.Typer(
    name="index-book",
    help="Deterministic legal-textbook indexer (book-indexer).",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


@app.command()
def build(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    rebuild_all: bool = typer.Option(False, "--rebuild-all"),
    rebuild_concepts: bool = typer.Option(False, "--rebuild-concepts"),
    rebuild_tables: bool = typer.Option(False, "--rebuild-tables"),
    rebuild_index_tree: bool = typer.Option(False, "--rebuild-index-tree"),
    sample_review: int = typer.Option(0, "--sample-review", min=0, max=200),
    verify_against: Path | None = typer.Option(None, "--verify-against"),
    allow_drift: int = typer.Option(0, "--allow-drift", min=0),
) -> None:
    """Run the full pipeline against PDF (default subcommand)."""
    raise typer.Exit(
        code=_build_mod.run(
            pdf=pdf,
            rebuild_all=rebuild_all,
            rebuild_concepts=rebuild_concepts,
            rebuild_tables=rebuild_tables,
            rebuild_index_tree=rebuild_index_tree,
            sample_review=sample_review,
            verify_against=verify_against,
            allow_drift=allow_drift,
        )
    )


@app.command(name="replay")
def replay_cmd() -> None:
    """QUAL-01: re-build into a tmpdir; diff every committed artifact byte-for-byte."""
    raise typer.Exit(code=_replay_mod.run())


@app.command(name="verify-against")
def verify_against_cmd(
    old_index_tree: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True
    ),
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    allow_drift: int = typer.Option(0, "--allow-drift", min=0),
) -> None:
    """CLI-02 / D-04: parse OLD_INDEX_TREE + new IR, compute entry-level diff."""
    raise typer.Exit(
        code=_verify_mod.run(
            old_index_tree=old_index_tree,
            pdf=pdf,
            allow_drift=allow_drift,
        )
    )


@app.command(name="review")
def review_cmd(sample: int = typer.Option(20, "--sample", min=1, max=200)) -> None:
    """QUAL-02: emit artifacts/audit/sample_review.md (deterministic stratified sample)."""
    raise typer.Exit(code=_review_mod.run(sample=sample))
