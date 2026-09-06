"""LanceDB implementation of the :class:`LexiconStore` Protocol.

See ``docs/lexicon-migration-spec.md`` §3-§4 for the design rationale.

Key design points
-----------------

* Single table ``terms`` (see :mod:`omniscribe.core.lexicon.schema`) —
  glossary-level metadata is denormalized into every row.
* HNSW vector index on the ``embedding`` column, built lazily on first
  query. Switch to IVF-PQ in the schema config for >100k entries.
* All writes are append-only on the row level. ``delete_glossary`` is a
  logical delete via LanceDB's filter expression; this doesn't fragment
  the index and is the right call for personal-scale lexicons.
* Reads use hybrid (vector + SQL filter) queries via LanceDB's
  ``.search().where()`` API.
* The store is process-safe; LanceDB handles per-process locking. For a
  single-user local app this is sufficient.
* No silent fallback: if lancedb is missing, we fail loud at first use
  with a clear install hint.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from .embedding import EmbeddingModel, get_default_embedding_model
from .lancedb_helpers import (
    _entry_from_row,
    _opt_str,
    _sql_escape,
    _to_utc_datetime,
)
from .schema import LEXICON_SCHEMA, VECTOR_INDEX_SPEC
from .store import (
    GlossaryMeta,
    LexiconEntry,
    LexiconHit,
    LexiconQuery,
    entry_hash,
    now_utc,
)

logger = logging.getLogger(__name__)


class EmbeddingModelMismatchError(RuntimeError):
    """The lexicon was built with a different embedding model.

    Cosine scores across mixed vector spaces are meaningless, so opening
    the store fails loud instead of returning silently wrong rankings.
    """


def _new_id() -> str:
    """Generate a new entry/glossary ID. UUID4 hex — matches the legacy format."""
    return uuid.uuid4().hex


def _row_from_entry(
    entry: dict[str, object],
    embedding: list[float],
    *,
    glossary_name: str,
    glossary_enabled: bool,
    glossary_priority: int,
    glossary_group: str,
    glossary_source_uri: str | None,
    glossary_encoding: str | None,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, object]:
    """Build a LanceDB row from an entry dict + its embedding.

    The caller supplies the denormalized glossary fields. The store is
    responsible for keeping them in sync on toggle/reorder.
    """
    return {
        "id": str(entry.get("id") or _new_id()),
        "glossary_id": str(entry["glossary_id"]),
        "source_text": str(entry["source"]),
        "target_text": str(entry["target"]),
        "source_lang": str(entry.get("source_lang", "")),
        "target_lang": str(entry.get("target_lang", "")),
        "domain": entry.get("domain"),
        "register": entry.get("register"),
        "pos": entry.get("pos"),
        "case_sensitive": bool(entry.get("case_sensitive", False)),
        "notes": str(entry.get("notes", "") or ""),
        "source_uri": entry.get("source_uri"),
        "source_format": str(entry.get("source_format", "json_pairs")),
        "usage_count": int(str(entry.get("usage_count", 0) or 0)),
        "entry_hash": entry_hash(str(entry["source"]), str(entry["target"])),
        "created_at": created_at,
        "updated_at": updated_at,
        "embedding": list(embedding),
        # Denormalized glossary metadata (spec §4.1) -----------------------------
        "glossary_name": glossary_name,
        "glossary_enabled": bool(glossary_enabled),
        "glossary_priority": int(glossary_priority),
        "glossary_group": str(glossary_group or "default"),
        "glossary_source_uri": glossary_source_uri,
        "glossary_encoding": glossary_encoding,
    }


# ---------------------------------------------------------------------------
# LanceDBLexiconStore
# ---------------------------------------------------------------------------


class LanceDBLexiconStore:
    """LanceDB-backed implementation of :class:`LexiconStore`.

    The store is process-safe. Construction does not open the database;
    the first call to any read/write method triggers lazy initialization
    (thread-safe via a one-shot lock).
    """

    TABLE_NAME = "terms"
    META_TABLE = "_meta"

    def __init__(
        self,
        *,
        path: Path,
        embedding_model: EmbeddingModel | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path).expanduser().resolve()
        self._path.mkdir(parents=True, exist_ok=True)
        self._embedding = embedding_model or get_default_embedding_model()
        self._clock = clock or now_utc
        self._db: Any = None
        self._table: Any = None
        self._init_lock = threading.Lock()
        self._initialized = False
        self._fingerprint_cache: str | None = None

    # --- Lifecycle ----------------------------------------------------------

    def _ensure_open(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            try:
                import lancedb
            except ImportError as exc:
                raise RuntimeError(
                    "LanceDBLexiconStore requires the `lancedb` package. "
                    "Install with: `uv sync --extra lexicon`."
                ) from exc
            self._db = lancedb.connect(str(self._path))
            # ``list_tables()`` returns a Pydantic response with a ``tables``
            # list (the older ``table_names()`` is deprecated). Handle both
            # shapes so we work across LanceDB 0.5 to 0.37.
            raw = self._db.list_tables()
            tables = getattr(raw, "tables", None)
            if tables is None:
                tables = list(raw)
            existing = {str(t) for t in tables}
            if self.TABLE_NAME in existing:
                self._table = self._db.open_table(self.TABLE_NAME)
            else:
                # Create empty table with the canonical schema; rows are
                # added on the first save_glossary call. ``mode="create"``
                # raises if the table already exists, which is what we want
                # here (the ``in existing`` branch above handles the
                # open-existing case).
                self._table = self._db.create_table(
                    self.TABLE_NAME, schema=LEXICON_SCHEMA, mode="create"
                )
            self._ensure_meta_and_compat(existing)
            self._ensure_columns()
            self._initialized = True
            logger.info("LanceDBLexiconStore opened at %s", self._path)

    def _ensure_meta_and_compat(self, existing_tables: set[str]) -> None:
        """Guard against embedding-model drift; adopt legacy tables.

        Records the model name + dim in a ``_meta`` table at creation time
        and compares on every open. A pre-``_meta`` lexicon adopts the
        currently-configured model (nothing to compare against yet).
        """
        import pyarrow as pa

        model_name = self._embedding.model_name
        dim = int(self._embedding.dim)
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
            "created_at": self._clock(),
        }
        if self.META_TABLE in existing_tables:
            meta = self._db.open_table(self.META_TABLE)
            rows = meta.to_arrow().to_pylist()
            if rows:
                stored_name = str(rows[0].get("model_name"))
                stored_dim = int(rows[0].get("dim") or 0)
                if stored_name != model_name or (stored_dim and stored_dim != dim):
                    raise EmbeddingModelMismatchError(
                        f"Lexicon at {self._path} was built with embedding model "
                        f"'{stored_name}' (dim={stored_dim}) but is being opened "
                        f"with '{model_name}' (dim={dim}). Vector spaces are "
                        "incompatible; re-import the glossaries or unset "
                        "OMNISCRIBE_EMBEDDING_MODEL."
                    )
                return
            meta.add([meta_row])
            return
        self._db.create_table(
            self.META_TABLE,
            pa.Table.from_pylist([meta_row], schema=meta_schema),
            mode="create",
        )

    def _ensure_columns(self) -> None:
        """Add columns introduced after the table was created (legacy tables)."""
        try:
            field_names = set(self._table.schema.names)  # type: ignore[union-attr]
        except Exception:
            return
        if "entry_hash" not in field_names:
            try:
                self._table.add_columns({"entry_hash": "NULL"})
                logger.info("Added entry_hash column to legacy lexicon table")
            except Exception as exc:
                logger.warning("Could not add entry_hash column: %s", exc)

    def close(self) -> None:
        # LanceDB connections are lightweight and process-bound; nothing to
        # explicitly close today. Kept for Protocol symmetry.
        self._initialized = False
        self._db = None
        self._table = None

    def health(self) -> dict[str, object]:
        self._ensure_open()
        try:
            tbl = self._table.to_arrow()
            row_count = tbl.num_rows
            if row_count > 0:
                import pyarrow.compute as pc

                glossary_count = len(pc.unique(tbl["glossary_id"]))
            else:
                glossary_count = 0
        except Exception:
            row_count = 0
            glossary_count = 0
        return {
            "path": str(self._path),
            "table": self.TABLE_NAME,
            "row_count": row_count,
            "glossary_count": glossary_count,
            "embedding_dim": self._embedding.dim,
            "embedding_model": self._embedding.model_name,
            "index_spec": VECTOR_INDEX_SPEC,
        }

    # --- Glossary library CRUD ----------------------------------------------

    def list_glossaries(self) -> list[GlossaryMeta]:
        self._ensure_open()
        # Project at the storage layer so the ``embedding`` column
        # (the largest in the table) is not loaded into memory —
        # the only thing this listing needs is the per-glossary
        # metadata columns plus the row count per glossary_id, both
        # of which come from a non-embedding column scan.
        # Audit catalog: push the column projection into the query
        # instead of materialising the full table and selecting in
        # pyarrow. Older ``lancedb`` versions don't accept
        # ``columns=`` on ``to_pandas``; fall back to the legacy
        # ``to_arrow() + select`` path on TypeError.
        columns = [
            "glossary_id",
            "glossary_name",
            "source_format",
            "glossary_source_uri",
            "glossary_encoding",
            "glossary_enabled",
            "glossary_priority",
            "glossary_group",
            "created_at",
            "updated_at",
        ]
        try:
            df = self._table.to_pandas(columns=columns)
            if df.empty:
                return []
            pylist = df.to_dict(orient="records")
        except TypeError:
            tbl = self._table.to_arrow()
            if tbl.num_rows == 0:
                return []
            available_cols = [c for c in columns if c in tbl.column_names]
            pylist = tbl.select(available_cols).to_pylist()
        groups: dict[str, dict[str, Any]] = {}
        for row in pylist:
            gid = str(row["glossary_id"])
            if gid not in groups:
                groups[gid] = {"first": row, "count": 1}
            else:
                groups[gid]["count"] += 1

        result: list[GlossaryMeta] = []
        for gid, grp in groups.items():
            first = grp["first"]
            result.append(
                GlossaryMeta(
                    id=gid,
                    name=str(first["glossary_name"]),
                    format=str(first["source_format"]),
                    source_uri=_opt_str(first.get("glossary_source_uri")),
                    encoding=_opt_str(first.get("glossary_encoding")),
                    enabled=bool(first["glossary_enabled"]),
                    priority=int(first["glossary_priority"]),
                    group=str(first["glossary_group"]),
                    entry_count=grp["count"],
                    created_at=_to_utc_datetime(first["created_at"]),
                    updated_at=_to_utc_datetime(first["updated_at"]),
                )
            )
        # Mirror the legacy sort: priority DESC, group, name, id.
        result.sort(
            key=lambda m: (-m.priority, m.group.casefold(), m.name.casefold(), m.id)
        )
        return result

    def get_glossary(self, glossary_id: str) -> GlossaryMeta | None:
        self._ensure_open()
        target = str(glossary_id)
        escaped_target = _sql_escape(target)
        try:
            search = self._table.search().where(f"glossary_id = '{escaped_target}'")
            tbl = search.to_arrow()
        except Exception:
            all_tbl = self._table.to_arrow()
            if all_tbl.num_rows == 0:
                return None
            import pyarrow.compute as pc

            mask = pc.equal(all_tbl["glossary_id"], target)
            tbl = all_tbl.filter(mask)
        if tbl.num_rows == 0:
            return None
        first = tbl.slice(0, 1).to_pylist()[0]
        return GlossaryMeta(
            id=target,
            name=str(first["glossary_name"]),
            format=str(first["source_format"]),
            source_uri=_opt_str(first.get("glossary_source_uri")),
            encoding=_opt_str(first.get("glossary_encoding")),
            enabled=bool(first["glossary_enabled"]),
            priority=int(first["glossary_priority"]),
            group=str(first["glossary_group"]),
            entry_count=tbl.num_rows,
            created_at=_to_utc_datetime(first["created_at"]),
            updated_at=_to_utc_datetime(first["updated_at"]),
        )

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
    ) -> GlossaryMeta:
        self._ensure_open()

        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("Glossary name is required.")
        if len(clean_name) > 200:
            raise ValueError("Glossary name must be at most 200 characters.")
        clean_format = str(format).strip().lower()
        if not clean_format:
            raise ValueError("Glossary format is required.")
        if isinstance(priority, bool):
            raise ValueError("Glossary priority must be an integer.")
        try:
            clean_priority = int(priority)
        except (TypeError, ValueError) as exc:
            raise ValueError("Glossary priority must be an integer.") from exc
        clean_group = str(group).strip() or "default"

        # Normalize entries: drop empty, drop junk, default language pair.
        normalized: list[dict[str, object]] = []
        for raw in entries:
            if not isinstance(raw, dict):
                continue
            src = str(raw.get("source", "")).strip()
            tgt = str(raw.get("target", "")).strip()
            if not src or not tgt:
                continue
            entry = dict(raw)
            entry["source"] = src
            entry["target"] = tgt
            # Defaults for fields that may not be present in the input
            entry.setdefault("source_lang", "")
            entry.setdefault("target_lang", "")
            entry.setdefault("source_format", clean_format)
            entry.setdefault("case_sensitive", False)
            entry.setdefault("notes", "")
            entry.setdefault("usage_count", 0)
            normalized.append(entry)
        if not normalized:
            raise ValueError("Glossary must contain at least one valid entry.")

        glossary_id = _new_id()
        now = self._clock()

        # Batch-embed the source_text for all entries. The embedding model
        # is process-cached so this is fast after the first call.
        source_texts: list[str] = [str(e["source"]) for e in normalized]
        embeddings = self._embedding.embed_batch(source_texts)
        if len(embeddings) != len(normalized):
            raise RuntimeError(
                f"Embedding model returned {len(embeddings)} vectors for "
                f"{len(normalized)} inputs."
            )

        rows = [
            _row_from_entry(
                {**e, "glossary_id": glossary_id},
                emb,
                glossary_name=clean_name,
                glossary_enabled=True,
                glossary_priority=clean_priority,
                glossary_group=clean_group,
                glossary_source_uri=str(source_uri) if source_uri else None,
                glossary_encoding=str(encoding) if encoding else None,
                created_at=now,
                updated_at=now,
            )
            for e, emb in zip(normalized, embeddings, strict=True)
        ]
        self._table.add(rows)
        logger.info(
            "Saved glossary %s (%s) with %d entries", glossary_id, clean_name, len(rows)
        )

        return GlossaryMeta(
            id=glossary_id,
            name=clean_name,
            format=clean_format,
            source_uri=str(source_uri) if source_uri else None,
            encoding=str(encoding) if encoding else None,
            enabled=True,
            priority=clean_priority,
            group=clean_group,
            entry_count=len(rows),
            created_at=now,
            updated_at=now,
        )

    def toggle_glossary(self, glossary_id: str, *, enabled: bool) -> GlossaryMeta:
        self._ensure_open()
        target = str(glossary_id)
        now = self._clock()
        new_value = bool(enabled)
        # Use a single SQL update statement — the denormalized glossary_enabled
        # field is what the hybrid filter reads, so we update it in place.
        escaped_target = _sql_escape(target)
        try:
            self._table.update(
                where=f"glossary_id = '{escaped_target}'",
                values={"glossary_enabled": new_value, "updated_at": now},
            )
        except Exception as exc:
            # Fallback path: per-row merge via Arrow table to re-write.
            tbl = self._table.to_arrow()
            if tbl.num_rows == 0:
                raise GlossaryNotFoundError(target) from exc
            records = tbl.to_pylist()
            found = False
            for r in records:
                if str(r.get("glossary_id")) == target:
                    r["glossary_enabled"] = new_value
                    r["updated_at"] = now
                    found = True
            if not found:
                raise GlossaryNotFoundError(target) from exc
            # C2 audit fix: build a fresh Arrow table from the updated
            # records and ``add`` it BEFORE deleting the originals so a
            # write failure leaves the original rows intact. If ``add``
            # raises, the original table is unchanged and no glossary
            # rows are lost.
            updated_arrow = pa.Table.from_pylist(records)
            try:
                self._table.add(updated_arrow)
            except Exception as add_exc:
                logger.error(
                    "toggle_glossary fallback failed to add updated rows for "
                    "glossary '%s': %s. Original rows preserved.",
                    target,
                    add_exc,
                )
                raise
            # Only delete the original partition after the new rows are
            # durably appended.
            self._table.delete(where=f"glossary_id = '{escaped_target}'")
        meta = self.get_glossary(target)
        if meta is None:
            raise GlossaryNotFoundError(target)
        return meta

    def reorder_glossaries(self, ordered_ids: Sequence[str]) -> None:
        self._ensure_open()
        ordered = [str(gid) for gid in ordered_ids]
        if not ordered:
            return
        # Assign priorities so that the first id in `ordered` gets the
        # highest priority (priority is sorted DESC in list_glossaries).
        # Total priority = len(ordered) - index.
        existing = {m.id for m in self.list_glossaries()}
        unknown = [gid for gid in ordered if gid not in existing]
        if unknown:
            raise GlossaryNotFoundError(unknown[0])
        total = len(ordered)
        for index, gid in enumerate(ordered):
            new_priority = total - index
            escaped_gid = _sql_escape(gid)
            self._table.update(
                where=f"glossary_id = '{escaped_gid}'",
                values={"glossary_priority": new_priority},
            )

    def delete_glossary(self, glossary_id: str) -> bool:
        self._ensure_open()
        target = str(glossary_id)
        escaped_target = _sql_escape(target)
        try:
            tbl = (
                self._table.search()
                .where(f"glossary_id = '{escaped_target}'")
                .limit(1)
                .to_arrow()
            )
            if tbl.num_rows == 0:
                return False
        except Exception:
            if self.get_glossary(target) is None:
                return False
        self._table.delete(where=f"glossary_id = '{escaped_target}'")
        return True

    # --- Read API (used by translation RAG) ---------------------------------

    def hybrid_query(self, query: LexiconQuery) -> list[LexiconHit]:
        self._ensure_open()
        if not query.source_chunk or not query.source_chunk.strip():
            return []
        try:
            row_count = self._table.count_rows()
        except Exception:
            row_count = self._table.to_arrow().num_rows
        if row_count == 0:
            return []

        # Embed the source chunk and search via LanceDB's native vector search.
        # Falls back to in-memory Arrow/NumPy cosine similarity if vector query fails.
        return self._hybrid_via_lancedb(query)

    def exact_lookup(
        self,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[LexiconEntry]:
        self._ensure_open()
        target_clean = source_text.strip().lower()
        if not target_clean:
            return []
        where_parts: list[str] = []
        if source_lang:
            where_parts.append(f"source_lang = '{_sql_escape(source_lang)}'")
        if target_lang:
            where_parts.append(f"target_lang = '{_sql_escape(target_lang)}'")
        try:
            search = self._table.search()
            if where_parts:
                search = search.where(" AND ".join(where_parts))
            tbl = search.to_arrow()
        except Exception:
            tbl = self._table.to_arrow()
        if tbl.num_rows == 0:
            return []

        entries: list[LexiconEntry] = []
        for row in tbl.to_pylist():
            if str(row.get("source_text", "")).strip().lower() == target_clean:
                if source_lang and str(row.get("source_lang", "")) != source_lang:
                    continue
                if target_lang and str(row.get("target_lang", "")) != target_lang:
                    continue
                entries.append(_entry_from_row(row))
        return entries

    def list_entries(self, glossary_id: str) -> list[LexiconEntry]:
        self._ensure_open()
        target = str(glossary_id)
        escaped_target = _sql_escape(target)
        try:
            search = self._table.search().where(f"glossary_id = '{escaped_target}'")
            tbl = search.to_arrow()
        except Exception:
            all_tbl = self._table.to_arrow()
            if all_tbl.num_rows == 0:
                return []
            import pyarrow.compute as pc

            mask = pc.equal(all_tbl["glossary_id"], target)
            tbl = all_tbl.filter(mask)
        if tbl.num_rows == 0:
            return []
        return [_entry_from_row(r) for r in tbl.to_pylist()]

    # --- Internal helpers ---------------------------------------------------

    def _matches_query(self, row: dict[str, Any], query: LexiconQuery) -> bool:
        """Evaluate query filter predicates against a row dict."""
        if query.source_lang and str(row.get("source_lang", "")) != query.source_lang:
            return False
        if query.target_lang and str(row.get("target_lang", "")) != query.target_lang:
            return False
        if query.domain and str(row.get("domain", "")) != query.domain:
            return False
        if query.enabled_only and not bool(row.get("glossary_enabled", True)):
            return False
        if query.glossary_ids is not None:
            allowed = {str(g) for g in query.glossary_ids}
            if str(row.get("glossary_id", "")) not in allowed:
                return False
        return True

    def _hybrid_via_lancedb(self, query: LexiconQuery) -> list[LexiconHit]:
        vector = self._embedding.embed(query.source_chunk)
        if not vector:
            return []
        try:
            search = (
                self._table.search(vector, vector_column_name="embedding")
                .metric("cosine")
                .limit(max(query.limit * 4, 16))  # over-fetch to absorb prefilter
            )
            where_clauses = self._build_where(query)
            if where_clauses:
                search = search.where(where_clauses)
            arrow_tbl = search.to_arrow()
            if arrow_tbl.num_rows == 0:
                return []
            raw = arrow_tbl.to_pylist()
        except Exception as exc:
            logger.warning(
                "LanceDB hybrid search failed: %s; falling back to Arrow/NumPy search",
                exc,
            )
            return self._hybrid_via_arrow(query)
        hits: list[LexiconHit] = []
        for row in raw:
            distance = float(row.get("_distance", 0.0))
            score = 1.0 - distance  # cosine distance → similarity
            if query.min_score is not None and score < query.min_score:
                continue
            entry = _entry_from_row(row)
            hits.append(LexiconHit(entry=entry, score=score))
            if len(hits) >= query.limit:
                break
        return hits

    def _hybrid_via_arrow(self, query: LexiconQuery) -> list[LexiconHit]:
        """Fallback ranking when ``_hybrid_via_lancedb`` failed.

        Audit catalog: push the supported subset of the WHERE clause
        into LanceDB before materialising the full table; the
        remaining predicates (``enabled_only``, ``glossary_ids``,
        and any filter ``_build_where`` rejected) still apply
        in-Python via :meth:`_matches_query`. This trims the row
        set the cosine pass has to score for the common case
        where the primary path's failure was unrelated to the
        filter (e.g. vector column missing, search index disabled).
        """
        import numpy as np

        try:
            where = self._build_where(query)
            if where:
                # Audit catalog: ``db_search().where(filter)`` instead
                # of materialising the entire table and filtering in
                # Python. Only used here when the primary path
                # already failed; we still keep the in-Python pass
                # for filter clauses ``_build_where`` does not cover.
                tbl = self._table.search().where(where).to_arrow()
            else:
                tbl = self._table.to_arrow()
        except Exception:
            try:
                tbl = self._table.to_arrow()
            except Exception:
                return []
        if tbl.num_rows == 0:
            return []

        rows = tbl.to_pylist()
        candidates = [r for r in rows if self._matches_query(r, query)]
        if not candidates:
            return []

        query_vec = np.asarray(
            self._embedding.embed(query.source_chunk), dtype=np.float32
        )
        emb_matrix = np.asarray([r["embedding"] for r in candidates], dtype=np.float32)
        # Cosine similarity
        qn = query_vec / (np.linalg.norm(query_vec) + 1e-12)
        en = emb_matrix / (np.linalg.norm(emb_matrix, axis=1, keepdims=True) + 1e-12)
        scores = en @ qn
        order = np.argsort(-scores)  # descending
        hits: list[LexiconHit] = []
        for idx in order:
            score = float(scores[idx])
            if query.min_score is not None and score < query.min_score:
                continue
            row = candidates[int(idx)]
            hits.append(LexiconHit(entry=_entry_from_row(row), score=score))
            if len(hits) >= query.limit:
                break
        return hits

    def _build_where(self, query: LexiconQuery) -> str | None:
        """Build a LanceDB WHERE clause string from the structured filters."""
        clauses: list[str] = []
        if query.source_lang:
            clauses.append(f"source_lang = '{_sql_escape(query.source_lang)}'")
        if query.target_lang:
            clauses.append(f"target_lang = '{_sql_escape(query.target_lang)}'")
        if query.domain:
            clauses.append(f"domain = '{_sql_escape(query.domain)}'")
        if query.enabled_only:
            clauses.append("glossary_enabled = true")
        if query.glossary_ids is not None:
            allowed = ", ".join(f"'{_sql_escape(str(g))}'" for g in query.glossary_ids)
            clauses.append(f"glossary_id IN ({allowed})")
        return " AND ".join(clauses) if clauses else None


class GlossaryNotFoundError(KeyError):
    """Raised when a requested glossary id does not exist in the store."""


__all__ = [
    "GlossaryNotFoundError",
    "LanceDBLexiconStore",
    "_row_from_entry",
]
