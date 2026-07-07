"""Evidence — the sole locator-emitting boundary object of the project.

Every Evidence instance is produced by exactly one call to ``verify()``
(Architecture Lock #1). The model is frozen + ``extra='forbid'`` so schema
drift is a ValidationError, not a silent data corruption. Tuple (not list)
for ``section_path`` preserves hashability under Pydantic v2's automatic
``__hash__`` generation — Evidence instances are set/dict keys in the
Plan 02-04 evidence-ledger ship-blocker.
"""
from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Matches §2, §2.04, §2.04.1 — no NBSP (rendering glue is Phase 5's concern per
# Phase 1 D-21 deferred note). Use r"^§\d+(\.\d{2}(\.\d+)?)?$".
_SECTION_REF_PATTERN = r"^§\d+(\.\d{2}(\.\d+)?)?$"


class Evidence(BaseModel):
    """Immutable Evidence row emitted by ``verify()`` (VER-04).

    Fields are exactly what Phase 2 ROADMAP Success Criterion 5 specifies;
    any extra field is a ``ValidationError`` of type ``extra_forbidden``.

    Frozen + all-hashable-fields → Pydantic v2 auto-generates ``__hash__``
    so instances are set/dict-safe. ``section_path`` is a tuple (not a list)
    specifically to preserve this hashability.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=False,  # snippets preserve whitespace verbatim
    )

    canonical_term:    Annotated[str, Field(min_length=1)]
    matched_variant:   Annotated[str, Field(min_length=1)]
    section_ref:       Annotated[str, Field(pattern=_SECTION_REF_PATTERN)]
    section_level:     Annotated[int, Field(ge=1, le=3)]
    section_path:      tuple[str, ...]
    folio:             str  # may be "" for blank pages (FOL allows empty)
    pdf_page:          Annotated[int, Field(ge=0)]
    token_offset:      Annotated[int, Field(ge=0)]
    match_mode:        Annotated[str, Field(pattern=r"^(exact|lemma|acronym)$")]
    verbatim_snippet:  Annotated[str, Field(min_length=60)]

    @field_validator("section_path", mode="before")
    @classmethod
    def _coerce_list_to_tuple(cls, v):
        """Accept list inputs from JSON deserialization; store as tuple."""
        return tuple(v) if isinstance(v, list) else v

    @model_validator(mode="after")
    def _check_section_path_consistency(self) -> Self:
        """D-08 cross-field: len(section_path)==section_level AND section_path[-1]==section_ref."""
        if len(self.section_path) != self.section_level:
            raise ValueError(
                f"section_path length {len(self.section_path)} "
                f"!= section_level {self.section_level}"
            )
        if self.section_path[-1] != self.section_ref:
            raise ValueError(
                f"section_path[-1]={self.section_path[-1]!r} "
                f"!= section_ref={self.section_ref!r}"
            )
        return self
