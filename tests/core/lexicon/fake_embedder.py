"""Shared deterministic fake embedding model for lexicon tests."""

from __future__ import annotations

import hashlib

from omniscribe.core.lexicon.embedding import EMBEDDING_DIM, EmbeddingModel


class HashEmbedder:
    """Deterministic, hash-based fake embedding model.

    Maps each text to a unit vector derived from the text hash. Two texts
    with the same source produce the same vector; similar prefixes produce
    somewhat-similar vectors (hash-bucketed). Not a real semantic model —
    just enough surface for the store to exercise vector search.
    """

    def __init__(self, model_name: str = "fake-test-model", dim: int = EMBEDDING_DIM):
        self.model_name = model_name
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            # Stretch the 32 bytes into the target dims by repeating.
            base = [b / 255.0 for b in digest] * ((self.dim // 32) + 1)
            vec = base[: self.dim]
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors


def unit_vector(index: int, dim: int = EMBEDDING_DIM) -> list[float]:
    """Orthogonal unit vector — one hot at ``index`` (for recall fixtures)."""
    vec = [0.0] * dim
    vec[index % dim] = 1.0
    return vec


class MappedEmbedder:
    """Fake embedder with an explicit term→vector mapping (recall tests).

    Unknown terms embed to the zero vector, so only mapped terms carry
    cosine signal — retrieval outcomes become exactly predictable.
    """

    def __init__(self, mapping: dict[str, list[float]], dim: int = EMBEDDING_DIM):
        self._mapping = mapping
        self.dim = dim
        self.model_name = "mapped-fake-model"

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [list(self._mapping.get(t, [0.0] * self.dim)) for t in texts]
