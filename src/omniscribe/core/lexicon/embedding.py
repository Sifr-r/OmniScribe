"""Embedding model ownership for the lexicon store.

The store owns the model; ``core/translate/workflow.py`` and the migration CLI
never import ``sentence-transformers`` directly. This keeps a single source
of truth for the embedding model version and dimension, and makes it easy
to swap the model in one place if ever needed (with a re-embed step on
existing data).

See ``docs/lexicon-migration-spec.md`` §5 for the rationale.

Pin the model — never silent-fallback to a different model. If the model
file is missing on first run, the store fails LOUD with a clear
"install the lexicon extra" message.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Pinned — see spec §5. Changing this requires re-embedding all existing data.
EMBEDDING_MODEL_NAME: str = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM: int = 384


@runtime_checkable
class EmbeddingModel(Protocol):
    """A minimal embedding model interface — small enough to mock in tests."""

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dim(self) -> int: ...
    @property
    def model_name(self) -> str: ...


class _SentenceTransformerEmbeddingModel:
    """Concrete :class:`EmbeddingModel` backed by ``sentence-transformers``.

    Lazily loads the model on first use. The model object is cached for the
    process lifetime via :func:`get_default_embedding_model`.
    """

    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: object | None = None

    def _load(self) -> object:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    f"Embedding model '{self._model_name}' requires the "
                    "sentence-transformers package. Install with: "
                    "`uv sync --extra lexicon`."
                ) from exc
            logger.info("Loading embedding model %s", self._model_name)
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._load()
        vector = model.encode([text], convert_to_numpy=True)[0]  # type: ignore[attr-defined]
        return [float(x) for x in vector]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        vectors = model.encode(texts, convert_to_numpy=True)  # type: ignore[attr-defined]
        return [[float(x) for x in vec] for vec in vectors]

    @property
    def dim(self) -> int:
        # Report the actually-loaded model's dimension, not the pinned
        # constant — with OMNISCRIBE_EMBEDDING_MODEL overrides the two can
        # differ, and health()/guards must not lie about the vector space.
        if self._model is not None:
            getter = getattr(
                self._model, "get_sentence_embedding_dimension", None
            )
            if callable(getter):
                dim = getter()
                if isinstance(dim, int) and dim > 0:
                    return dim
        return EMBEDDING_DIM

    @property
    def model_name(self) -> str:
        return self._model_name


@lru_cache(maxsize=1)
def get_default_embedding_model() -> EmbeddingModel:
    """Process-lazy singleton for the default embedding model.

    The model name can be overridden by the ``OMNISCRIBE_EMBEDDING_MODEL``
    env var (escape hatch for tests; production should use the pinned
    default).
    """
    override = os.getenv("OMNISCRIBE_EMBEDDING_MODEL")
    name = override.strip() if override else EMBEDDING_MODEL_NAME
    return _SentenceTransformerEmbeddingModel(name)


__all__ = [
    "EMBEDDING_DIM",
    "EMBEDDING_MODEL_NAME",
    "EmbeddingModel",
    "get_default_embedding_model",
]
