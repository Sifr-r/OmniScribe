"""PyArrow schema for the LanceDB ``terms`` table.

See ``docs/lexicon-migration-spec.md`` §4 for the design rationale and the
write-amplification trade-off (denormalized glossary metadata into every row
to avoid joins on the hot translation RAG path).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pyarrow as pa

logger = logging.getLogger(__name__)


class EmbeddingModelMismatchError(RuntimeError):
    """The lexicon was built with a different embedding model.

    Cosine scores across mixed vector spaces are meaningless, so opening
    the store fails loud instead of returning silently wrong rankings.
    """


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


class LexiconSchemaManager:
    """Encapsulates PyArrow schemas, table initialization, migrations, and indexing."""

    TABLE_NAME: str = "terms"
    META_TABLE: str = "_meta"
    INDEX_MIN_ROWS: int = 128

    def __init__(
        self,
        schema: pa.Schema = LEXICON_SCHEMA,
        index_spec: dict[str, object] | None = None,
        min_index_rows: int = INDEX_MIN_ROWS,
    ) -> None:
        self._schema = schema
        self._index_spec = index_spec or VECTOR_INDEX_SPEC
        self._min_index_rows = min_index_rows

    def ensure_table(
        self,
        db: Any,
        embedding_model: Any,
        clock: Callable[[], datetime],
    ) -> Any:
        """Open or initialize the canonical terms table, verifying metadata and schema."""
        raw = db.list_tables()
        tables = getattr(raw, "tables", None)
        if tables is None:
            tables = list(raw)
        existing = {str(t) for t in tables}
        if self.TABLE_NAME in existing:
            table = db.open_table(self.TABLE_NAME)
        else:
            table = db.create_table(self.TABLE_NAME, schema=self._schema, mode="create")
        self.ensure_meta_and_compat(db, existing, embedding_model, clock)
        self.ensure_columns(table)
        self.ensure_index(table)
        return table

    def ensure_meta_and_compat(
        self,
        db: Any,
        existing_tables: set[str],
        embedding_model: Any,
        clock: Callable[[], datetime],
    ) -> None:
        """Guard against embedding-model drift; adopt legacy tables."""
        model_name = embedding_model.model_name
        dim = int(embedding_model.dim)
        meta_schema = pa.schema(
            [
                pa.field("model_name", pa.string(), nullable=False),
                pa.field("dim", pa.int32(), nullable=False),
                pa.field("created_at", pa.timestamp("ms"), nullable=False),
            ]
        )
        meta_row = {
            "model_name": model_name,
            "dim": dim,
            "created_at": clock(),
        }
        if self.META_TABLE in existing_tables:
            meta = db.open_table(self.META_TABLE)
            rows = meta.to_arrow().to_pylist()
            if rows:
                stored_name = str(rows[0].get("model_name"))
                stored_dim = int(rows[0].get("dim") or 0)
                if stored_name != model_name or (stored_dim and stored_dim != dim):
                    raise EmbeddingModelMismatchError(
                        f"Lexicon was built with embedding model "
                        f"'{stored_name}' (dim={stored_dim}) but is being opened "
                        f"with '{model_name}' (dim={dim}). Vector spaces are "
                        "incompatible; re-import the glossaries or unset "
                        "OMNISCRIBE_EMBEDDING_MODEL."
                    )
                return
            meta.add([meta_row])
            return
        db.create_table(
            self.META_TABLE,
            pa.Table.from_pylist([meta_row], schema=meta_schema),
            mode="create",
        )

    def ensure_columns(self, table: Any) -> None:
        """Add columns introduced after the table was created (legacy tables)."""
        try:
            field_names = set(table.schema.names)
        except Exception:
            return
        if "entry_hash" not in field_names:
            try:
                table.add_columns(pa.schema([pa.field("entry_hash", pa.string())]))
                logger.info("Added entry_hash column to legacy lexicon table")
            except Exception as exc:
                logger.warning("Could not add entry_hash column: %s", exc)

    def ensure_index(self, table: Any) -> None:
        """Create the HNSW (or IVF-PQ) vector index per index_spec."""
        try:
            if table.count_rows() < self._min_index_rows:
                return
            index_type = str(self._index_spec["index_type"])
            kwargs: dict[str, object] = {
                "metric": self._index_spec["metric"],
                "vector_column_name": "embedding",
                "index_type": index_type,
                "replace": True,
            }
            if index_type == "ivf_pq":
                kwargs["num_partitions"] = self._index_spec["num_partitions"]
                kwargs["num_sub_vectors"] = self._index_spec["num_sub_vectors"]
            table.create_index(**kwargs)
            logger.info("Vector index ensured (%s)", index_type)
        except Exception as exc:
            logger.debug("create_index skipped: %s", exc)


__all__ = [
    "LEXICON_SCHEMA",
    "VECTOR_INDEX_SPEC",
    "EmbeddingModelMismatchError",
    "LexiconSchemaManager",
]
