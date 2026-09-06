"""Lexicon store — single embedded columnar vector database for the canonical glossary.

This package is the home for the OmniScribe lexicon store. See
``docs/lexicon-migration-spec.md`` for the full design.

Public surface
--------------

- :class:`LexiconStore` — the Protocol every caller reads through (spec §3)
- :class:`LexiconEntry`, :class:`LexiconQuery`, :class:`LexiconHit`,
  :class:`GlossaryMeta` — the data shapes
- :class:`LanceDBLexiconStore` — the default embedded implementation
- :func:`get_default_embedding_model` — process-lazy embedding model singleton
- :func:`merged_enabled_glossary`, :func:`preview` — composition helpers
- :class:`GlossaryNotFoundError` — raised when a glossary is missing
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from .embedding import (
    EMBEDDING_DIM,
    EMBEDDING_MODEL_NAME,
    EmbeddingModel,
    get_default_embedding_model,
)
from .helpers import (
    GlossaryNotFoundError,
    merged_enabled_glossary,
    preview,
)
from .lancedb_store import (
    EmbeddingModelMismatchError,
    LanceDBLexiconStore,
)
from .store import (
    GlossaryMeta,
    LexiconEntry,
    LexiconHit,
    LexiconQuery,
    LexiconStore,
    entry_hash,
    normalize_term,
    now_utc,
)

logger = logging.getLogger(__name__)


# Default artifact dir resolution: matches the existing artifact_dir layout
# in the server. Override with ``OMNISCRIBE_ARTIFACT_DIR`` (server startup
# env) or pass an explicit ``path=`` to :func:`get_default_store`.
_DEFAULT_ARTIFACT_DIR = (
    Path(os.getenv("OMNISCRIBE_ARTIFACT_DIR") or "./omniscribe_artifacts")
    .expanduser()
    .resolve()
)


def _default_lexicon_path() -> Path:
    return _DEFAULT_ARTIFACT_DIR / "lexicon.lance"


@lru_cache(maxsize=1)
def get_default_store() -> LexiconStore:
    """Process-lazy singleton for the default :class:`LexiconStore`.

    Returns a :class:`LanceDBLexiconStore` rooted at
    ``$OMNISCRIBE_ARTIFACT_DIR/lexicon.lance`` (default
    ``./omniscribe_artifacts/lexicon.lance``). Used by the translation
    graph's ``retrieve_lexicon_context`` node.

    The store is opened lazily on first call. Subsequent calls return the
    cached instance. Tests should pass an explicit ``path=`` to a
    temporary location rather than relying on the env var.
    """
    return LanceDBLexiconStore(
        path=_default_lexicon_path(),
        embedding_model=get_default_embedding_model(),
    )


def reset_default_store() -> None:
    """Clear the cached default store (for tests that swap the env var)."""
    get_default_store.cache_clear()


__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL_NAME",
    "EmbeddingModel",
    "EmbeddingModelMismatchError",
    "GlossaryMeta",
    "GlossaryNotFoundError",
    "LanceDBLexiconStore",
    "LexiconEntry",
    "LexiconHit",
    "LexiconQuery",
    "LexiconStore",
    "entry_hash",
    "get_default_embedding_model",
    "get_default_store",
    "merged_enabled_glossary",
    "normalize_term",
    "now_utc",
    "preview",
    "reset_default_store",
]
