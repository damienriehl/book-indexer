"""Query-side tokenization for verify() — reuses the Phase 1 tokenizer.

RESEARCH §Pitfall 1 (lemma-pipeline drift): the verifier MUST lemmatize
queries through the same cached spaCy pipeline + legal phrase overrides
that built ``tokens.lemma``. Calling the raw spaCy pipeline directly
(i.e., ``nlp.__call__``) skips the phrase-merger ContextVar priming and
drifts on multi-word Latin phrases — always go through ``nlp_call``.

The module exposes:
  - ``QueryToken`` — frozen+slots dataclass with (norm, lemma).
  - ``tokenize_query(term)`` — returns list[QueryToken] in left-to-right order.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from book_indexer.ingest.normalizer import normalize
from book_indexer.ingest.tokenizer import load_tokenizer, nlp_call

# config/legal_lemma_overrides.yaml lives three directories above this file
# (src/book_indexer/verify/query_tokenizer.py → parents[3] == repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_LEGAL_OVERRIDES = _REPO_ROOT / "config" / "legal_lemma_overrides.yaml"


@dataclass(frozen=True, slots=True)
class QueryToken:
    """One tokenized unit of a verifier query.

    ``norm`` matches ``tokens.norm`` from Phase 1's corpus; ``lemma`` matches
    ``tokens.lemma``. Both fields are lowercased (``normalize`` lowercases;
    spaCy's lemmatizer output is lowercased by our pipeline configuration).
    """
    norm: str
    lemma: str


def tokenize_query(term: str) -> list[QueryToken]:
    """Tokenize a verifier query identically to how Phase 1 built the corpus.

    Returns a list of QueryToken in left-to-right order. Whitespace-only
    tokens are dropped so the matcher can do a position-by-position
    comparison against ``tokens.norm`` / ``tokens.lemma``.

    Raises:
        ValueError: if ``term`` is empty or whitespace-only.
    """
    if not term or not term.strip():
        raise ValueError("term must be non-empty")

    nlp = load_tokenizer(_LEGAL_OVERRIDES)
    doc = nlp_call(nlp, term)

    out: list[QueryToken] = []
    for tok in doc:
        if tok.is_space or not tok.text:
            continue
        norm_v = normalize(tok.text)
        lemma_v = (tok.lemma_ or tok.text).lower()
        if not norm_v:
            continue
        out.append(QueryToken(norm=norm_v, lemma=lemma_v))
    return out
