"""spaCy-backed tokenization with legal-lemma overrides (D-17, D-18).

Loading strategy (per plan 01-04 Task 4.1 and RESEARCH.md §F):
  - ``spacy.require_cpu()`` is called at module import — GPU determinism is
    hostile; CPU is required for byte-identical re-runs.
  - ``en_core_web_lg`` loads with ``parser`` / ``ner`` / ``attribute_ruler``
    disabled (we only need the tokenizer + lemmatizer).
  - A registered ``legal_phrase_merger`` component runs BEFORE the lemmatizer
    and retokenizes YAML-configured phrases into single tokens with the
    override lemma attached. This handles the phrase-level override tier.
  - Token-level overrides (``media`` / ``dicta`` → canonical lemmas) are
    applied in a post-pass over the resulting :class:`Doc`.

The module caches the loaded ``Language`` per overrides-path so repeated
:func:`tokenize_block` calls do not reload the 500MB model.

Determinism note: ``nlp.vocab[...]`` is a read-only lookup, the phrase merger
uses :func:`spacy.util.filter_spans` for deterministic span filtering, and
token-override application walks tokens in insertion order. Tests assert
that two runs produce identical :class:`TokenRecord` lists.
"""
from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from pathlib import Path

import spacy
import yaml
from spacy.language import Language
from spacy.matcher import PhraseMatcher
from spacy.tokens import Doc

from .normalizer import normalize
from .types import BlockClassification

spacy.require_cpu()  # pyright: ignore[reportPrivateImportUsage]

# W108 is emitted by spaCy's rule-based lemmatizer when POS tags are missing
# for some tokens — which happens for punctuation or out-of-vocab tokens even
# with the tagger enabled. The lemmatizer falls back to the token text, which
# is the acceptable behavior for our corpus. Silence the warning here; the
# pyproject.toml filterwarnings=["error"] would otherwise convert it to an
# exception during ingest.
warnings.filterwarnings(
    "ignore",
    message=r".*\[W108\].*",
    category=UserWarning,
    module=r"spacy\.pipeline\.lemmatizer",
)


@dataclass(frozen=True)
class TokenRecord:
    """One output row of the tokenizer; serialized into ``tokens`` by the
    corpus writer. Fields are verbatim sans the FK ``section_id`` which is
    assigned post-insert by :func:`corpus_writer.assign_section_ids`.
    """
    pdf_page: int
    token_index: int           # 0-based within the page
    text: str                  # verbatim for evidence snippets
    norm: str                  # normalize()'d surface form (FTS5 index key)
    lemma: str                 # spaCy lemma or legal override
    block_type: str            # "body" | "footnote"
    block_role: str | None
    bbox: tuple[float, float, float, float]
    font_size: float
    font_name: str
    crosses_page_break: int    # 0 | 1


# Module-level cache: reuse one Language per overrides-path across calls.
_NLP_CACHE: dict[str, Language] = {}


# Track the set of previously-registered factory names so spaCy does not raise
# "factory 'legal_phrase_merger' already registered" when the test suite
# reloads the module (which can happen under pytest's collection sequence).
_FACTORY_REGISTERED = False


def _load_overrides(yaml_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(phrase_to_lemma, token_surface_to_lemma)``.

    Both keys are lowercased for case-insensitive matching; the lemma value
    is taken verbatim from the YAML (typically lowercase canonical form).
    """
    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    phrases = {
        str(p["phrase"]).lower(): str(p["lemma"])
        for p in (data.get("phrases") or [])
    }
    tokens = {
        str(k).lower(): str(v)
        for k, v in (data.get("tokens") or {}).items()
    }
    return phrases, tokens


# Default fixture path used by attach_phrase_overrides_to_meta below; importers
# (concepts/assembly/render __main__.py) can pass a custom path for tests.
_DEFAULT_LEGAL_OVERRIDES_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "legal_lemma_overrides.yaml"
)


def attach_phrase_overrides_to_meta(
    nlp: Language,
    overrides_yaml: Path | None = None,
) -> Language:
    """Populate ``nlp.meta["_legal_phrase_overrides"]`` from the YAML fixture.

    B-10 cross-callsite plumbing (CONTEXT 06 D-02; Plan 06-02 architectural
    finding): downstream pipelines (concepts / assembly / render) call
    ``spacy.load("en_core_web_lg")`` directly — they DO NOT use
    ``load_tokenizer`` and therefore never populate the
    ``_legal_phrase_overrides`` meta key that the 3 short-circuit callsites
    (``canonical_form_key``, ``normalize_for_lemma`` + ``lemma_bucket_key``,
    ``synthesize_bare_lemma_entries``) read. Without this helper, the
    short-circuits would never trigger and ``cross-examination`` would
    re-fragment back to ``cross - examination`` despite the Phase 1 phrase
    merger's single-token corpus output.

    Idempotent: re-attaching with the same path is a no-op for downstream
    consumers (they read the same dict). Returns ``nlp`` for chaining.
    """
    path = overrides_yaml or _DEFAULT_LEGAL_OVERRIDES_PATH
    if path.is_file():
        phrase_overrides, token_overrides = _load_overrides(path)
    else:
        phrase_overrides, token_overrides = {}, {}
    nlp.meta["_legal_phrase_overrides"] = phrase_overrides
    nlp.meta["_legal_token_overrides"] = token_overrides
    return nlp


def load_tokenizer(overrides_yaml: Path | None = None) -> Language:
    """Load ``en_core_web_lg`` + register ``legal_phrase_merger``.

    Pipeline layout:
      1. tagger (enabled — cheap; feeds lemmatizer features)
      2. legal_phrase_merger (our custom component; retokenizes phrases)
      3. lemmatizer
    Disabled components (parser / ner / attribute_ruler) are skipped entirely.

    The returned ``Language`` instance is cached per ``overrides_yaml`` path.
    Calling again with the same path returns the same object; callers that
    need isolation should pass unique paths or import-reset the module.
    """
    cache_key = str(overrides_yaml.resolve()) if overrides_yaml else "<none>"
    if cache_key in _NLP_CACHE:
        return _NLP_CACHE[cache_key]

    # Load the model. ``attribute_ruler`` is a post-tagger that we do NOT
    # need for lemmatization of legal text; skipping it saves a couple
    # hundred ms per 1k tokens.
    nlp = spacy.load(
        "en_core_web_lg",
        disable=["parser", "ner", "attribute_ruler"],
    )

    phrase_overrides: dict[str, str] = {}
    token_overrides: dict[str, str] = {}
    if overrides_yaml is not None and Path(overrides_yaml).is_file():
        phrase_overrides, token_overrides = _load_overrides(Path(overrides_yaml))

    # Build the PhraseMatcher with LOWER attribute so we match case-insensitively.
    matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    patterns = [nlp.make_doc(phrase) for phrase in phrase_overrides]
    if patterns:
        matcher.add("LEGAL_PHRASES", patterns)

    # Register the component factory ONCE per process. spaCy's registry raises
    # if you re-register with the same name; the module-level flag prevents
    # that on re-imports.
    global _FACTORY_REGISTERED

    if not _FACTORY_REGISTERED:
        @Language.factory("legal_phrase_merger")
        def _factory(nlp: Language, name: str):  # noqa: ARG001 - spaCy contract requires these names
            return _LegalPhraseMergerComponent()
        _FACTORY_REGISTERED = True

    # Instance config: we attach the matcher + phrase_overrides on the nlp
    # meta so the factory-produced component can read them. (Factories must
    # not close over per-call state or the component becomes non-serializable.)
    nlp.meta["_legal_phrase_matcher"] = matcher
    nlp.meta["_legal_phrase_overrides"] = phrase_overrides
    nlp.meta["_legal_token_overrides"] = token_overrides

    # Add the component before the lemmatizer so merges happen first.
    if "legal_phrase_merger" not in nlp.pipe_names:
        if "lemmatizer" in nlp.pipe_names:
            nlp.add_pipe("legal_phrase_merger", before="lemmatizer")
        else:
            nlp.add_pipe("legal_phrase_merger", last=True)

    _NLP_CACHE[cache_key] = nlp
    return nlp


class _LegalPhraseMergerComponent:
    """Callable spaCy pipeline component (factory-produced).

    Stateless — reads matcher + overrides off ``nlp.meta`` at call time so a
    rebuilt pipeline still finds the right data.
    """

    def __call__(self, doc: Doc) -> Doc:
        nlp = doc.vocab  # vocab ref; real state is on the originating Language.
        # Fetch state from the Language.meta (we stash it there in load_tokenizer).
        matcher: PhraseMatcher | None = None
        overrides: dict[str, str] = {}
        # The Doc doesn't hold a ref to its creating Language, but we can
        # access the vocab's strings table to detect "do we have anything to merge?"
        # We rely on ``doc._.legal_phrase_matcher`` style or extension — simpler:
        # read from the stored meta on the first language found via the vocab.
        # spaCy >=3 ships Doc.vocab but not a back-ref to the Language; we route
        # state via a contextvars stash instead.
        state = _MERGER_STATE.get()
        if state is None:
            return doc
        matcher, overrides = state
        if matcher is None or not overrides:
            return doc
        matches = matcher(doc)
        if not matches:
            return doc
        spans = [doc[start:end] for _mid, start, end in matches]
        spans = spacy.util.filter_spans(spans)
        if not spans:
            return doc
        with doc.retokenize() as retokenizer:
            for span in spans:
                key = span.text.lower()
                lemma = overrides.get(key, span.text.lower())
                retokenizer.merge(span, attrs={"LEMMA": lemma})
        _ = nlp  # quiet lint
        return doc


# Module-level context for the merger component to read matcher + overrides.
# Using a ContextVar avoids closing over a specific Language (which would break
# the factory contract) while keeping state thread/task-safe.
from contextvars import ContextVar  # noqa: E402 - intentional late import

_MERGER_STATE: ContextVar[tuple[PhraseMatcher, dict[str, str]] | None] = ContextVar(
    "_legal_phrase_merger_state", default=None
)


def _apply_token_overrides(doc: Doc, token_overrides: dict[str, str]) -> None:
    """Post-pass: for every token whose lowercased surface form is keyed in
    ``token_overrides``, replace ``.lemma_`` with the override value."""
    if not token_overrides:
        return
    for tok in doc:
        key = tok.text.lower()
        if key in token_overrides:
            tok.lemma_ = token_overrides[key]


def _block_spans(block: dict) -> list[dict]:
    """Flatten every span inside every line of a block, preserving order."""
    out: list[dict] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            out.append(span)
    return out


def _first_non_empty_span(block: dict) -> dict | None:
    for span in _block_spans(block):
        if (span.get("text") or "").strip():
            return span
    return None


def _block_bbox(block: dict) -> tuple[float, float, float, float]:
    bb = block.get("bbox", (0.0, 0.0, 0.0, 0.0))
    return tuple(round(float(v), 2) for v in bb)  # type: ignore[return-value]


def _run_nlp_with_overrides(nlp: Language, text: str) -> Doc:
    """Run the pipeline with the phrase-merger state primed via ContextVar.

    Suppresses spaCy's W108 warning (``The rule-based lemmatizer did not
    find POS annotation for one or more tokens``) at call time. With
    ``attribute_ruler`` disabled per D-17, a handful of tokens (punctuation,
    some out-of-vocab words) arrive at the lemmatizer without a POS tag;
    the lemmatizer's fallback is the token text — acceptable for our corpus
    but otherwise gets promoted to an error by the ``filterwarnings=["error"]``
    setting in pyproject.toml during pytest.
    """
    matcher = nlp.meta.get("_legal_phrase_matcher")
    phrase_overrides = nlp.meta.get("_legal_phrase_overrides") or {}
    token_overrides = nlp.meta.get("_legal_token_overrides") or {}
    token = _MERGER_STATE.set((matcher, phrase_overrides) if matcher else None)
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*\[W108\].*",
                category=UserWarning,
            )
            doc = nlp(text)
    finally:
        _MERGER_STATE.reset(token)
    _apply_token_overrides(doc, token_overrides)
    return doc


def nlp_call(nlp: Language, text: str) -> Doc:
    """Public wrapper — same as ``nlp(text)`` but primes the phrase-merger
    state so tests that pass strings directly behave identically to
    :func:`tokenize_block`.

    Callers from the test suite (where the prior docstring implied a plain
    ``nlp(...)`` call) can prefer this helper; direct ``nlp(...)`` is still
    fine — the component gracefully no-ops when the ContextVar is empty
    (matches nothing and returns the doc unchanged).
    """
    return _run_nlp_with_overrides(nlp, text)


def tokenize_block(
    nlp: Language,
    pdf_page: int,
    block: dict,
    block_classification: BlockClassification,
    starting_token_index: int,
) -> list[TokenRecord]:
    """Tokenize a single PyMuPDF text block into :class:`TokenRecord` rows.

    Skips blocks whose classification is not ``body`` or ``footnote``
    (``header_footer``, ``image``, anything else → empty list). Whitespace-only
    spaCy tokens are filtered out.

    Bbox / font metadata is attributed at block granularity (Phase 1 scope);
    per-span attribution is a future refinement that would require coupling
    tokenizer output back to span offsets — out of scope here.
    """
    if block_classification.block_type not in ("body", "footnote"):
        return []

    spans = _block_spans(block)
    if not spans:
        return []
    block_text = "".join(span.get("text", "") for span in spans)
    if not block_text.strip():
        return []

    doc = _run_nlp_with_overrides(nlp, block_text)

    first_span = _first_non_empty_span(block) or (spans[0] if spans else None)
    font_size = float(first_span["size"]) if first_span else 0.0
    font_name = str(first_span.get("font", "")) if first_span else ""
    bbox = _block_bbox(block)

    out: list[TokenRecord] = []
    idx = starting_token_index
    for tok in doc:
        if not tok.text or tok.is_space:
            continue
        text = tok.text
        out.append(
            TokenRecord(
                pdf_page=pdf_page,
                token_index=idx,
                text=text,
                norm=normalize(text),
                lemma=(tok.lemma_ or text).lower(),
                block_type=block_classification.block_type,
                block_role=block_classification.block_role,
                bbox=bbox,
                font_size=font_size,
                font_name=font_name,
                crosses_page_break=0,
            )
        )
        idx += 1
    return out


def spacy_model_sha256() -> str:
    """SHA-256 over every file in the installed ``en_core_web_lg`` package.

    Stored in ``extraction_metadata.spacy_model_sha256`` for provenance. The
    hash is order-stable: we walk ``rglob('*')`` sorted by path, hashing the
    filename then bytes. A lemmatizer-version drift produces a new digest,
    which downstream QUAL-01 uses to detect cache-invalidating changes.
    """
    import en_core_web_lg  # pyright: ignore[reportMissingImports]

    assert en_core_web_lg.__file__ is not None
    model_dir = Path(en_core_web_lg.__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(model_dir.rglob("*")):
        if p.is_file():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()
