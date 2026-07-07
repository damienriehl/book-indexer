"""Architecture Lock #3: no Anthropic-SDK imports anywhere in src/.

``src/book_indexer/concepts/subagent.py`` is the ONLY pathway to the
Claude CLI; it calls ``subprocess.run(["claude", "-p", ...])``. Any module
that imports ``anthropic`` or ``claude_agent_sdk`` bypasses the Max-subscription
billing model and violates CON-01.

Enforced by scanning every ``.py`` under ``src/book_indexer/`` for
``import anthropic``, ``from anthropic import ...``, ``import claude_agent_sdk``,
and ``from claude_agent_sdk import ...`` via AST. NO EXCLUSIONS — zero
SDK imports anywhere in src.

Includes a self-test that writes a rogue synthetic file and confirms the
scanner fires — proves the visitor actually catches violations rather than
silently passing empty scans.

requirements_addressed: CON-01
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.invariants


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "book_indexer"

_FORBIDDEN_MODULES: frozenset[str] = frozenset({"anthropic", "claude_agent_sdk"})


class _SdkImportVisitor(ast.NodeVisitor):
    """AST walker flagging any import from forbidden SDK modules."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[tuple[int, str, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top = alias.name.split(".", 1)[0]
            if top in _FORBIDDEN_MODULES:
                self.violations.append((node.lineno, "import", alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            top = node.module.split(".", 1)[0]
            if top in _FORBIDDEN_MODULES:
                self.violations.append((node.lineno, "from_import", node.module))
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Parse ``path`` and return violations as ``(lineno, kind, name)`` tuples."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise AssertionError(f"failed to parse {path}: {exc}") from exc
    visitor = _SdkImportVisitor(str(path))
    visitor.visit(tree)
    return visitor.violations


def _files_to_scan() -> list[Path]:
    """Every ``.py`` file under ``src/book_indexer/``. No exclusions."""
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_no_sdk_imports_in_src_book_indexer() -> None:
    """Architecture Lock #3: zero ``anthropic`` / ``claude_agent_sdk`` imports in ``src/``."""
    files = _files_to_scan()
    assert files, f"no .py files found under {_SRC_ROOT} — scanner is broken"
    all_violations: list[tuple[Path, int, str, str]] = []
    for path in files:
        for lineno, kind, name in _scan_file(path):
            all_violations.append((path, lineno, kind, name))
    if all_violations:
        msg = "\n".join(
            f"  {p.relative_to(_REPO_ROOT)}:{ln}: {kind} {name!r}"
            for p, ln, kind, name in all_violations
        )
        raise AssertionError(
            "Architecture Lock #3 violated — Anthropic SDK imports in src/:\n" + msg
        )


def test_scanner_catches_import_statement(tmp_path: Path) -> None:
    """Self-test: write a rogue file and confirm the scanner fires."""
    rogue = tmp_path / "rogue.py"
    rogue.write_text(
        "# pragma: no cover\n"
        "import anthropic\n"
        "from claude_agent_sdk import ClaudeAgent\n"
        "import os  # this one is fine\n",
        encoding="utf-8",
    )
    violations = _scan_file(rogue)
    assert len(violations) == 2
    assert violations[0][1:] == ("import", "anthropic")
    assert violations[1][1:] == ("from_import", "claude_agent_sdk")


def test_scanner_ignores_benign_imports(tmp_path: Path) -> None:
    """False-positive guard: clearly-benign top-level packages are NOT flagged."""
    benign = tmp_path / "benign.py"
    benign.write_text(
        "import os\n"
        "import subprocess\n"
        "from pydantic import BaseModel\n"
        "from somepkg.anthropic_helper import X  # top-level 'somepkg' — fine\n"
        "import anthropic_lookalike  # different top-level package — fine\n",
        encoding="utf-8",
    )
    violations = _scan_file(benign)
    assert violations == []


def test_scanner_catches_dotted_imports(tmp_path: Path) -> None:
    """``anthropic.types`` is still forbidden — top-level split matches."""
    dotted = tmp_path / "dotted.py"
    dotted.write_text(
        "from anthropic.types import Message\n"
        "import anthropic.foo\n",
        encoding="utf-8",
    )
    violations = _scan_file(dotted)
    assert len(violations) == 2
    assert violations[0][1] == "from_import"
    assert violations[0][2] == "anthropic.types"
    assert violations[1][1] == "import"
    assert violations[1][2] == "anthropic.foo"
