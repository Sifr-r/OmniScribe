"""PyArrow schema for the LanceDB ``terms`` table.

See ``docs/lexicon-migration-spec.md`` §4 for the design rationale and the
write-amplification trade-off (denormalized glossary metadata into every row
to avoid joins on the hot translation RAG path).
"""

from __future__ import annotations

import pyarrow as pa

# Single-column table — every row is one (source, target) term pair.
# Glossary-level metadata is denormalized (glossary_name, glossary_enabled,
# glossary_priority, glossary_group) so that hybrid queries can filter on
# these fields without a join.
LEXICON_SCHEMA: pa.Schema = pa.schema(
    [
        pa.field("id", pa.string(), nullable=False),
        pa.field("glossary_id", pa.string(), nullable=False),
        pa.field("source_text", pa.string(), nullable=False),
        pa.field("target_text", pa.string(), nullable=False),
        pa.field("source_lang", pa.string(), nullable=False),
        pa.field("target_lang", pa.string(), nullable=False),
        pa.field("domain", pa.string(), nullable=True),
        pa.field("register", pa.string(), nullable=True),
        pa.field("pos", pa.string(), nullable=True),
        pa.field("case_sensitive", pa.bool_(), nullable=False),
        pa.field("notes", pa.string(), nullable=True),
        pa.field("source_uri", pa.string(), nullable=True),
        pa.field("source_format", pa.string(), nullable=False),
        pa.field("usage_count", pa.int64(), nullable=False),
        # Content hash (source\x1ftarget) for embedding reuse on re-import.
        # Nullable so pre-existing tables can adopt the column lazily; rows
        # written after this column's introduction always set it.
        pa.field("entry_hash", pa.string(), nullable=True),
        pa.field("created_at", pa.timestamp("ms"), nullable=False),
        pa.field("updated_at", pa.timestamp("ms"), nullable=False),
        pa.field(
            "embedding",
            pa.list_(pa.float32(), 384),
            nullable=False,
        ),
        # Denormalized glossary metadata (see spec §4.1) ------------------------
        pa.field("glossary_name", pa.string(), nullable=False),
        pa.field("glossary_enabled", pa.bool_(), nullable=False),
        pa.field("glossary_priority", pa.int32(), nullable=False),
        pa.field("glossary_group", pa.string(), nullable=False),
        pa.field("glossary_source_uri", pa.string(), nullable=True),
        pa.field("glossary_encoding", pa.string(), nullable=True),
    ]
)


# Vector index configuration. HNSW is the right default for a single-user
# local app with up to ~100k terms; IVF-PQ is a config-time swap for larger
# corpora (smaller on disk, slightly lower recall).
VECTOR_INDEX_SPEC: dict[str, object] = {
    "metric": "cosine",
    "index_type": "hnsw",  # override to "ivf_pq" if entry_count > 100k
    "num_partitions": 64,  # IVF-PQ only
    "num_sub_vectors": 48,  # IVF-PQ only
}


__all__ = ["LEXICON_SCHEMA", "VECTOR_INDEX_SPEC"]
