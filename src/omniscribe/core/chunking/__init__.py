"""RAG-ready chunking and element taxonomy subsystem."""

from omniscribe.core.chunking.chunker import (
    ChunkingError,
    DocumentChunk,
    SectionAwareChunker,
    chunk_tree,
)
from omniscribe.core.chunking.taxonomy import (
    RAGElementCategory,
    map_element_type,
)

__all__ = [
    "ChunkingError",
    "DocumentChunk",
    "RAGElementCategory",
    "SectionAwareChunker",
    "chunk_tree",
    "map_element_type",
]
