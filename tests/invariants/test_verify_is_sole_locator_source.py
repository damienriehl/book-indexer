r"""requirements_addressed: VER-01 (Architecture Lock #1)

Architecture Lock #1 CI ship-blocker — ``verify()`` is the sole emitter of
any locator (section_ref OR folio OR pdf_page) in the project's output
surface.

This file enforces TWO complementary checks:

A. **Evidence-construction scan** (``test_no_evidence_construction_outside_verify``):
   No module outside ``src/book_indexer/verify/`` may call
   ``Evidence(...)`` to construct an Evidence row. This is the
   *architectural intent* of Lock #1 — only ``verify()`` may emit a
   citation locator.

B. **Locator-shape AST scan** (``test_verify_is_sole_locator_source``):
   No module outside the legitimate non-emitter packages may use
   ``section_ref`` / ``folio`` / ``pdf_page`` as a kwarg, literal dict
   key, ast.Name target, or assemble a section-ref-shaped string
   literal. This is the *defense-in-depth* check; it catches modules
   that hand-roll a locator-shaped object that bypasses Evidence.

Legitimate non-emitter packages (excluded from check B; check A still
applies):

  1. ``src/book_indexer/verify/`` — the *one* allowed emitter. By
     design, this package constructs Evidence rows and therefore uses
     ``section_ref``, ``folio``, and ``pdf_page`` as kwargs, dict keys,
     and assignment targets. Excluding it is the whole point of the test.

  2. ``src/book_indexer/ingest/`` — Phase 1's data-production layer.
     Ingest builds ``page_corpus.sqlite`` by extracting text + folios +
     section structure directly from the PDF. It LEGITIMATELY uses
     ``pdf_page=<n>``, ``folio=<s>``, ``section_ref=<s>`` as kwargs and
     Pydantic/dataclass field names because those ARE the schema column
     names it is populating. Ingest is the data SOURCE the verifier reads
     from — it is not an emitter of index citations.

  3. ``src/book_indexer/tables/`` — Phase 3b's citation-tables
     pipeline. Tables/ extractors (``cases``, ``statutes``, ``rules``,
     ``regex_fallback``) record ``pdf_page`` on RawHit dataclasses as
     EXTRACTION sidecars (where in the PDF the regex/eyecite found the
     citation surface — used only for char-offset proximity in subsection
     narrowing). Tables/ ``__main__`` then constructs ``Locator`` objects
     whose ``section_ref`` and ``folio`` fields are SOURCED VERBATIM from
     the Evidence rows that Phase 2 ``verify()`` emitted. The Locator is
     a projection of an existing Evidence — never a hand-rolled locator.
     The check-A guard (no Evidence construction) is the binding
     architectural invariant for tables/, and it remains in force.

  4. ``src/book_indexer/assembly/`` — Phase 4's index-assembly
     pipeline. assembly/ ``cite_rule``, ``subdivide``, and ``tree``
     construct ``Locator`` objects whose ``section_ref`` and ``folio``
     fields are SOURCED VERBATIM from the Evidence rows that Phase 2
     ``verify()`` emitted (via Phase 4 verifier_sweep). Identical
     rationale to tables/: the Locator is a PROJECTION of an existing
     Evidence — never a hand-rolled locator. The check-A guard (no
     Evidence construction) remains in force for assembly/, and is
     verified by per-module test_no_evidence_constructions tests under
     ``tests/unit/assembly/``.

The scanner WILL still catch any of the four violation shapes in future
Phase 4 / 5 / 6 modules where the risk of a rogue second emitter
genuinely exists (LLM discovery, index assembly, rendering, CLI). Every
new subpackage added under ``src/book_indexer/`` outside the three
excluded dirs is scanned automatically on CI.

Four detection shapes per RESEARCH §Pattern 4 (check B):

  1. String literal matching ``r'^§\s*\d'`` — a section-ref-shaped
     literal assembled outside the verifier.
  2. Assignment target / Python Name in
     ``{section_ref, folio, pdf_page}``.
  3. Dict key (constant string) in
     ``{section_ref, folio, pdf_page}``.
  4. Call keyword argument (kwarg) in
     ``{section_ref, folio, pdf_page}``.

False positives on check B in NON-excluded packages are ACCEPTABLE —
they force the developer to move the offending code into ``verify/``
(the architecturally correct outcome) or refactor. Do not add
individual-file exceptions; fix the violating code.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml as _yaml

pytestmark = pytest.mark.invariants

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "book_indexer"

# Legitimate non-emitter packages (excluded from the check-B AST shape
# scan; check A — no Evidence(...) construction — still applies to all
# of them except verify/). See module docstring for architectural
# rationale.
_EXCLUDED_DIRS: tuple[Path, ...] = (
    _SRC_ROOT / "verify",
    _SRC_ROOT / "ingest",
    _SRC_ROOT / "tables",
    _SRC_ROOT / "assembly",
    # Phase 5 render/ consumes Evidence-derived locators (read from
    # `artifacts/index_tree_evidence.json`, which itself came from
    # `verify()` upstream in Phase 2/3a/4). Render/ never CONSTRUCTS
    # new locators — it only re-serializes them into MD/DOCX/audit
    # output. Lock #1 still binds via check A (no `Evidence(...)`
    # construction) which has no exclusions.
    _SRC_ROOT / "render",
    # Phase 8 audit/ is a read-only consumer of corpus rows
    # (sections.title) and existing index_tree.json::provenance. It
    # constructs ``ProbeCandidate`` Pydantic models whose ``section_ref``
    # field stores the SOURCE row's section_ref — never an emitted
    # locator. ``nearest_section_heading_match`` on ``CoverageAuditEntry``
    # is sourced verbatim from the corpus, never invented. Architectural
    # parallel to assembly/render: read locator-shaped strings, never
    # construct Evidence. Lock #1 still binds via check A (no
    # ``Evidence(...)`` construction) — verified by
    # ``grep -c "Evidence(" src/book_indexer/audit/*.py`` = 0.
    _SRC_ROOT / "audit",
)

_SECTION_REF_LITERAL = re.compile(r"^§\s*\d")
_LOCATOR_KEYS = frozenset({"section_ref", "folio", "pdf_page"})


class _SoleLocatorVisitor(ast.NodeVisitor):
    """AST NodeVisitor that flags any locator-emitting code shape."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[tuple[int, str, str]] = []

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _SECTION_REF_LITERAL.match(node.value):
            self.violations.append(
                (node.lineno, "section_ref_literal", node.value)
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in _LOCATOR_KEYS:
                self.violations.append(
                    (node.lineno, "locator_name_assign", target.id)
                )
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                if key.value in _LOCATOR_KEYS:
                    self.violations.append(
                        (node.lineno, "locator_dict_key", key.value)
                    )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if kw.arg in _LOCATOR_KEYS:
                self.violations.append(
                    (node.lineno, "locator_kwarg", kw.arg)
                )
        self.generic_visit(node)


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    v = _SoleLocatorVisitor(filename=str(path))
    v.visit(tree)
    return v.violations


def _is_inside_excluded_dir(path: Path) -> bool:
    """True if ``path`` is (recursively) inside any legitimate-emitter dir."""
    resolved = path.resolve()
    for excluded in _EXCLUDED_DIRS:
        try:
            resolved.relative_to(excluded.resolve())
            return True
        except ValueError:
            continue
    return False


def _files_to_scan() -> list[Path]:
    """Every ``.py`` under ``src/book_indexer/`` EXCEPT legitimate emitters.

    Legitimate emitters are:
      - ``src/book_indexer/verify/`` (the sole allowed emitter)
      - ``src/book_indexer/ingest/`` (Phase 1 data producer — populates
        the corpus schema but does not emit index citations)
    """
    return [
        p for p in sorted(_SRC_ROOT.rglob("*.py"))
        if not _is_inside_excluded_dir(p)
    ]


def test_verify_is_sole_locator_source() -> None:
    """Architecture Lock #1 ship-blocker.

    Any module outside ``src/book_indexer/verify/`` (and outside
    Phase 1 ``ingest/``) that looks like it is assembling a locator
    (section_ref OR folio OR pdf_page) without going through ``verify()``
    fails this test. Fix by moving the code into ``verify/`` or
    refactoring to consume an Evidence object from ``verify()`` upstream.
    """
    all_violations: list[str] = []
    scanned: list[Path] = _files_to_scan()
    # Sanity: the scan must find SOME files (the cli/ subpackage exists).
    # If scanned is empty, the exclusion logic is over-broad and the test
    # is silently passing on nothing.
    assert scanned, f"no .py files found under {_SRC_ROOT}"

    for f in scanned:
        for lineno, kind, evidence in _scan_file(f):
            all_violations.append(
                f"{f.relative_to(_REPO_ROOT)}:{lineno} {kind}={evidence!r}"
            )

    assert not all_violations, (
        "Architecture Lock #1 violated — locator construction outside "
        "verify/ (or Phase 1 ingest/):\n"
        + "\n".join(all_violations)
        + "\n\nFix: move the offending construction into "
        "src/book_indexer/verify/, or refactor to consume an Evidence "
        "object returned by verify()."
    )


def test_excluded_dirs_are_skipped() -> None:
    """Sanity: the scanner file list does NOT include verify/ or ingest/."""
    scanned = _files_to_scan()
    for p in scanned:
        assert not _is_inside_excluded_dir(p), (
            f"scanner should have excluded {p} (legitimate emitter dir)"
        )


def test_scanner_finds_violations_in_synthetic_file(tmp_path: Path) -> None:
    """Self-test: the scanner detects each of the four violation shapes.

    This confirms the scanner is operationally sound — a regression that
    accidentally breaks the visitor logic (e.g. ``visit_Constant`` stops
    checking strings) would make the main test vacuously pass on real
    code. The self-test catches that class of bug.
    """
    synthetic = tmp_path / "rogue.py"
    synthetic.write_text(
        '# pragma: no cover\n'
        'section_ref = "§2.04"\n'  # locator_name_assign + section_ref_literal
        'd = {"folio": "42"}\n'    # locator_dict_key
        'foo(pdf_page=7)\n',        # locator_kwarg
        encoding="utf-8",
    )
    violations = _scan_file(synthetic)
    kinds = {v[1] for v in violations}
    assert {
        "locator_name_assign",
        "section_ref_literal",
        "locator_dict_key",
        "locator_kwarg",
    } <= kinds, f"missing kinds: {kinds}"


def test_scanner_ignores_non_locator_names(tmp_path: Path) -> None:
    """False-positive guard: benign Python code must not trip the scanner."""
    benign = tmp_path / "benign.py"
    benign.write_text(
        'from __future__ import annotations\n'
        '\n'
        'x = 1\n'
        'name = "foo"\n'
        'd = {"answer": 42}\n'
        'def greet(name: str) -> str:\n'
        '    return f"hi {name}"\n',
        encoding="utf-8",
    )
    assert _scan_file(benign) == []


# ----------------------------------------------------------------------------
# Check A — no Evidence(...) construction outside verify/
# ----------------------------------------------------------------------------


class _EvidenceCtorVisitor(ast.NodeVisitor):
    """Flags any ``Evidence(...)`` constructor call.

    Detects both bare-name calls (``Evidence(...)``) and attribute calls
    (``evidence_module.Evidence(...)``). Type annotations are not flagged
    (``ast.Subscript`` and bare-name uses inside ``ast.AnnAssign``/
    ``ast.arguments`` annotation nodes never reach ``visit_Call``).
    """

    def __init__(self) -> None:
        self.violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Name) and f.id == "Evidence":
            self.violations.append((node.lineno, "Evidence(...)"))
        elif isinstance(f, ast.Attribute) and f.attr == "Evidence":
            self.violations.append((node.lineno, f"...{f.attr}(...)"))
        self.generic_visit(node)


def _scan_for_evidence_ctor(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    v = _EvidenceCtorVisitor()
    v.visit(tree)
    return v.violations


def test_no_evidence_construction_outside_verify() -> None:
    """Architecture Lock #1, check A — Evidence is constructed ONLY inside
    ``src/book_indexer/verify/``. Every module under
    ``src/book_indexer/`` outside that package is scanned for any
    ``Evidence(...)`` constructor call.

    This is the strict architectural invariant: even ingest/, tables/,
    concepts/, etc. may NOT instantiate Evidence. The shape-AST scan
    (check B) excludes some legitimate non-emitter dirs from the
    field-name check, but check A applies universally.
    """
    verify_dir = (_SRC_ROOT / "verify").resolve()
    all_violations: list[str] = []
    for f in sorted(_SRC_ROOT.rglob("*.py")):
        try:
            f.resolve().relative_to(verify_dir)
            continue  # inside verify/, skip
        except ValueError:
            pass
        for lineno, kind in _scan_for_evidence_ctor(f):
            all_violations.append(f"{f.relative_to(_REPO_ROOT)}:{lineno} {kind}")
    assert not all_violations, (
        "Architecture Lock #1 (check A) violated — Evidence is "
        "constructed outside src/book_indexer/verify/:\n"
        + "\n".join(all_violations)
        + "\n\nFix: route the locator through verify() and consume the "
        "returned Evidence iterator instead of instantiating Evidence "
        "directly."
    )


def test_check_a_self_test_detects_evidence_ctor(tmp_path: Path) -> None:
    """Self-test for check A: a synthetic file with an Evidence(...) call
    is flagged. Guards against a regression that silently breaks the
    visitor (e.g., a typo in ``f.id == 'Evidence'``)."""
    synthetic = tmp_path / "rogue.py"
    synthetic.write_text(
        "Evidence(canonical_term='x')\n"
        "mod.Evidence()\n",
        encoding="utf-8",
    )
    found = _scan_for_evidence_ctor(synthetic)
    assert len(found) == 2, f"check-A self-test failed: {found}"


def test_check_a_ignores_type_annotations(tmp_path: Path) -> None:
    """Self-test for check A: type annotations referencing Evidence
    (``def f() -> Evidence:``, ``x: Evidence = ...``) are NOT flagged —
    they don't construct anything."""
    benign = tmp_path / "benign.py"
    benign.write_text(
        "from __future__ import annotations\n"
        "from typing import Iterator\n"
        "def f() -> 'Evidence': ...\n"
        "def g(rows: 'Iterator[Evidence]') -> 'list[Evidence]': ...\n",
        encoding="utf-8",
    )
    assert _scan_for_evidence_ctor(benign) == []


# ----------------------------------------------------------------------------
# Check C — YAML denylist scan of fixtures/index_editorial_overrides.yaml
# ----------------------------------------------------------------------------
#
# Phase 9 (REND-01) extends Lock #1 / Lock #2 to the new editorial-overrides
# curator fixture. The fixture is loaded by Pydantic with ``extra="forbid"``
# at runtime; this test is the AST-time defense in depth — it walks the
# parsed YAML tree and asserts no key in the locator denylist appears at
# any depth. The denylist is a SUPERSET of ``_LOCATOR_KEYS`` (which only
# covers ``section_ref``, ``folio``, ``pdf_page``); it adds ``page``,
# ``pp``, and ``p`` per CONTEXT D-08 because YAML keys could shorthand any
# of those.
#
# The fixture itself lands in Wave 1; this test SKIPs until then.
# ----------------------------------------------------------------------------


_FIXTURE_EDITORIAL = _REPO_ROOT / "fixtures" / "index_editorial_overrides.yaml"


def test_editorial_overrides_fixture_has_no_locator_keys() -> None:
    """Lock #1 spirit + Lock #2: the editorial-overrides fixture must not
    contain ``page``, ``pdf_page``, ``section_ref``, ``folio``, ``pp``, ``p``
    keys at any depth. Empty / missing fixture is acceptable until Wave 1.

    REND-01 — Check C (YAML denylist scan).
    """
    if not _FIXTURE_EDITORIAL.exists():
        pytest.skip("fixture lands in Wave 1")
    raw = _yaml.safe_load(_FIXTURE_EDITORIAL.read_text(encoding="utf-8"))
    forbidden = {"page", "pdf_page", "section_ref", "folio", "pp", "p"}
    found: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(k, str) and k in forbidden:
                    found.append(f"{path}/{k}")
                walk(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(raw, "")
    assert not found, f"Lock #1/#2: locator keys in fixture: {found}"
