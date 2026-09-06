"""Lexicon store Protocol — the single read/write surface for the canonical glossary.

See ``docs/lexicon-migration-spec.md`` §3 for the design rationale and the
caller-mapping table.

The Protocol is :func:`typing.runtime_checkable` so concrete implementations
(e.g. :class:`omniscribe.core.lexicon.LanceDBLexiconStore`) can be verified
structurally. Callers depend on this Protocol, not on any concrete class.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

# Embedding model dimension is fixed by the pinned model in ``embedding.py``.
# We re-export the constant here so callers don't need to import the embedding
# module just to validate or construct a query.
EMBEDDING_DIM: int = 384


def normalize_term(text: str) -> str:
    """NFC + casefold — the single normalization for term comparison.

    Used by the merge/preview helpers and the keyword leg so "Straße"
    and "STRASSE" (and NFD/NFC spellings) resolve to one key everywhere.
    """
    return unicodedata.normalize("NFC", text).strip().casefold()


def entry_hash(source: str, target: str) -> str:
    """Content hash of a term pair, for embedding reuse across re-imports.

    Deliberately case-sensitive: a re-import that changes casing produced
    a different entry and must be re-embedded rather than silently reusing
    the old vector.
    """
    return hashlib.sha256(f"{source}\x1f{target}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LexiconEntry:
    """One source→target term pair, as stored in the canonical store.

    Mirrors the legacy ``GlossaryEntry`` shape but adds glossary-level
    metadata (``glossary_id``) so the store can be queried by glossary.
    """

    id: str
    glossary_id: str
    source_text: str
    target_text: str
    source_lang: str
    target_lang: str
    domain: str | None
    register: str | None
    pos: str | None
    case_sensitive: bool
    notes: str
    source_uri: str | None
    source_format: str
    usage_count: int
    created_at: datetime
    updated_at: datetime

    def to_prompt_block_line(self) -> str:
        """Format as a DeepL-style ``style_rules`` line for the LLM prompt."""
        return f"- {self.source_text} -> {self.target_text}"


@dataclass(frozen=True, slots=True)
class LexiconQuery:
    """Hybrid query: vector similarity + structured filters.

    The store applies ``source_lang`` / ``target_lang`` / ``domain`` /
    ``enabled_only`` / ``glossary_ids`` as SQL pre-filters and then runs a
    vector search over the filtered set. This is the natural shape of a
    translation RAG lookup.
    """

    source_chunk: str
    source_lang: str | None = None
    target_lang: str | None = None
    domain: str | None = None
    enabled_only: bool = True
    glossary_ids: Sequence[str] | None = None
    limit: int = 3
    min_score: float | None = None


@dataclass(frozen=True, slots=True)
class LexiconHit:
    """A single result from a :class:`LexiconQuery`.

    ``score`` is the vector-leg cosine similarity clamped to 0.0-1.0;
    ``keyword_score`` is the deterministic keyword evidence (1.0 exact,
    0.8 prefix, 0.6 substring, 0.0 absent).
    """

    entry: LexiconEntry
    score: float
    keyword_score: float = 0.0


@dataclass(frozen=True, slots=True)
class GlossaryMeta:
    """Glossary-level metadata.

    Denormalized into every row in the store (see spec §4.1) so that
    glossary-level filters (enabled, priority, group) work without a join
    on the hot translation RAG path.
    """

    id: str
    name: str
    format: str
    source_uri: str | None
    encoding: str | None
    enabled: bool
    priority: int
    group: str
    entry_count: int
    created_at: datetime
    updated_at: datetime


def now_utc() -> datetime:
    """Timezone-aware UTC now — used for ``created_at`` / ``updated_at``."""
    return datetime.now(UTC)


@runtime_checkable
class LexiconStore(Protocol):
    """The single read/write surface for the canonical glossary/lexicon.

    Concrete implementations: :class:`omniscribe.core.lexicon.LanceDBLexiconStore`
    (the default embedded implementation). Future: an in-memory implementation
    for tests and ephemeral use.
    """

    # --- Glossary library CRUD ----------------------------------------------
    def list_glossaries(self) -> list[GlossaryMeta]: ...
    def get_glossary(self, glossary_id: str) -> GlossaryMeta | None: ...
    def save_glossary(
        self,
        *,
        name: str,
        format: str,
        entries: Iterable[dict[str, object]],
        source_uri: str | None = None,
        encoding: str | None = None,
        group: str = "default",
        priority: int = 0,
        glossary_id: str | None = None,
        upsert: bool = False,
    ) -> GlossaryMeta: ...
    def toggle_glossary(self, glossary_id: str, *, enabled: bool) -> GlossaryMeta: ...
    def reorder_glossaries(self, ordered_ids: Sequence[str]) -> None: ...
    def delete_glossary(self, glossary_id: str) -> bool: ...

    # --- Read API (used by translation RAG) ---------------------------------
    def hybrid_query(self, query: LexiconQuery) -> list[LexiconHit]: ...
    def exact_lookup(
        self,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[LexiconEntry]: ...
    def list_entries(self, glossary_id: str) -> list[LexiconEntry]: ...

    # --- Maintenance --------------------------------------------------------
    def fingerprint(self) -> str:
        """Cheap content fingerprint of the glossary library.

        Stable until any save/toggle/delete mutation. Lets callers cache
        derived artifacts (e.g. translations) keyed on lexicon state.
        """
        ...

    def health(self) -> dict[str, object]: ...
    def close(self) -> None: ...


__all__ = [
    "EMBEDDING_DIM",
    "GlossaryMeta",
    "LexiconEntry",
    "LexiconHit",
    "LexiconQuery",
    "LexiconStore",
    "entry_hash",
    "normalize_term",
    "now_utc",
]
