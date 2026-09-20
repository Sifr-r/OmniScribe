"""LanceDB implementation of the :class:`LexiconStore` Protocol.

See ``docs/lexicon-migration-spec.md`` §3-§4 for the design rationale.

Key design points
-----------------

* Single table ``terms`` (see :mod:`omniscribe.core.lexicon.schema`) —
  glossary-level metadata is denormalized into every row.
* HNSW vector index on the ``embedding`` column, created at open and
  re-ensured after bulk imports (>= 128 rows). Switch to IVF-PQ in the
  schema config for >100k entries.
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

import hashlib
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
    _opt_str,
    _sql_escape,
    _to_utc_datetime,
)
from .schema import (
    LEXICON_SCHEMA,
    VECTOR_INDEX_SPEC,
    EmbeddingModelMismatchError,
    LexiconSchemaManager,
)
from .search import HybridSearchEngine
from .store import (
    GlossaryMeta,
    GlossaryNotFoundError,
    LexiconEntry,
    LexiconHit,
    LexiconQuery,
    entry_hash,
    now_utc,
)

logger = logging.getLogger(__name__)


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
    INDEX_MIN_ROWS = 128

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
        self._schema_mgr = LexiconSchemaManager(
            schema=LEXICON_SCHEMA,
            index_spec=VECTOR_INDEX_SPEC,
            min_index_rows=self.INDEX_MIN_ROWS,
        )
        self._search_engine = HybridSearchEngine()

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
            self._table = self._schema_mgr.ensure_table(
                self._db, self._embedding, self._clock
            )
            self._initialized = True
            logger.info("LanceDBLexiconStore opened at %s", self._path)

    def close(self) -> None:
        # LanceDB connections are lightweight and process-bound; nothing to
        # explicitly close today. Kept for Protocol symmetry.
        self._initialized = False
        self._db = None
        self._table = None

    def _ensure_index(self) -> None:
        """Create the HNSW (or IVF-PQ) vector index per VECTOR_INDEX_SPEC."""
        self._schema_mgr.ensure_index(self._table)

    def fingerprint(self) -> str:
        """Cheap content fingerprint of the glossary library (Protocol).

        Cached in-process; invalidated by save/toggle/delete/reorder.
        """
        if self._fingerprint_cache is not None:
            return self._fingerprint_cache
        try:
            metas = self.list_glossaries()
        except Exception:
            return "unavailable"
        payload = "|".join(
            f"{m.id}:{m.name}:{m.entry_count}:{int(m.enabled)}"
            for m in sorted(metas, key=lambda m: m.id)
        )
        self._fingerprint_cache = hashlib.sha256(payload.encode("utf-8")).hexdigest()[
            :16
        ]
        return self._fingerprint_cache

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
        index_status: object = "unknown"
        try:
            indices = self._table.list_indices()
            names = [getattr(i, "name", str(i)) for i in indices]
            index_status = names if names else "none"
        except Exception:
            pass
        return {
            "path": str(self._path),
            "table": self.TABLE_NAME,
            "row_count": row_count,
            "glossary_count": glossary_count,
            "embedding_dim": self._embedding.dim,
            "embedding_model": self._embedding.model_name,
            "index_spec": VECTOR_INDEX_SPEC,
            "index_status": index_status,
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
        glossary_id: str | None = None,
        upsert: bool = False,
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

        now = self._clock()

        # Resolve the glossary id: explicit id wins (legacy migration /
        # re-save under the same id -- save_glossary deletes prior rows for
        # that id first, so re-runs are idempotent); upsert replaces the
        # glossary with the same (name, source_uri) instead of duplicating.
        # Reusable embeddings are captured BEFORE the delete so unchanged
        # entries don't pay a re-embed.
        resolved_id = str(glossary_id).strip() if glossary_id else ""
        reusable: dict[str, list[float]] = {}
        if resolved_id:
            reusable = self._embeddings_by_entry_hash(resolved_id)
            self._table.delete(where=f"glossary_id = '{_sql_escape(resolved_id)}'")
        elif upsert:
            existing = self._find_by_name_and_uri(
                clean_name, str(source_uri) if source_uri else None
            )
            if existing is not None:
                resolved_id = existing.id
                reusable = self._embeddings_by_entry_hash(existing.id)
                self._table.delete(where=f"glossary_id = '{_sql_escape(existing.id)}'")
        glossary_id = resolved_id or _new_id()

        # Batch-embed the source_text for all entries, reusing stored
        # embeddings for unchanged (source, target) pairs so a corrected
        # re-import only embeds the diff. The embedding model is
        # process-cached so this is fast after the first call.
        source_texts: list[str] = [str(e["source"]) for e in normalized]
        hashes = [entry_hash(str(e["source"]), str(e["target"])) for e in normalized]
        missing_idx = [i for i, h in enumerate(hashes) if h not in reusable]
        fresh = self._embedding.embed_batch([source_texts[i] for i in missing_idx])
        if len(fresh) != len(missing_idx):
            raise RuntimeError(
                f"Embedding model returned {len(fresh)} vectors for "
                f"{len(missing_idx)} inputs."
            )
        embeddings: list[list[float]] = [
            list(reusable[h]) if h in reusable else [] for h in hashes
        ]
        for slot, i in enumerate(missing_idx):
            embeddings[i] = fresh[slot]

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
        self._fingerprint_cache = None
        self._ensure_index()
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
        self._fingerprint_cache = None
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
        self._fingerprint_cache = None

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
        self._fingerprint_cache = None
        return True

    # --- Read API (used by translation RAG) ---------------------------------

    # Hybrid retrieval (LLM-remediation wave): the "hybrid" query used to be
    # vector-only — acronyms, model numbers, and CJK proper nouns (a
    # glossary's bread and butter) are exactly what cosine-only search
    # misses. The query now fuses:
    #   vector leg — cosine ANN over the chunk (truncated to the embedding
    #     window) plus extracted candidate terms, per-entry max;
    #   keyword leg — deterministic normalized exact/prefix/substring match
    #     (deliberately no FTS/tantivy dependency; CJK-safe);
    # fused by reciprocal rank fusion with env-tunable leg weights.

    def hybrid_query(self, query: LexiconQuery) -> list[LexiconHit]:
        self._ensure_open()
        return self._search_engine.hybrid_search(self._table, query, self._embedding)

    def exact_lookup(
        self,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[LexiconEntry]:
        self._ensure_open()
        return self._search_engine.exact_lookup(
            self._table,
            source_text,
            source_lang=source_lang,
            target_lang=target_lang,
        )

    def list_entries(self, glossary_id: str) -> list[LexiconEntry]:
        self._ensure_open()
        return self._search_engine.list_entries(self._table, glossary_id)

    # --- Internal helpers ---------------------------------------------------

    def _find_by_name_and_uri(
        self, name: str, source_uri: str | None
    ) -> GlossaryMeta | None:
        target = name.casefold()
        for meta in self.list_glossaries():
            if meta.name.casefold() != target:
                continue
            if (meta.source_uri or None) != (source_uri or None):
                continue
            return meta
        return None

    def _embeddings_by_entry_hash(self, glossary_id: str) -> dict[str, list[float]]:
        """Map entry_hash -> embedding for one glossary's existing rows."""
        if not glossary_id:
            return {}
        try:
            tbl = (
                self._table.search()
                .where(f"glossary_id = '{_sql_escape(glossary_id)}'")
                .to_arrow()
            )
            if tbl.num_rows == 0 or "entry_hash" not in tbl.column_names:
                return {}
            return {
                str(r["entry_hash"]): list(r["embedding"])
                for r in tbl.select(["entry_hash", "embedding"]).to_pylist()
                if r.get("entry_hash")
            }
        except Exception:
            return {}

    def _build_where(self, query: LexiconQuery) -> str | None:
        return self._search_engine.build_where(query)


__all__ = [
    "EmbeddingModelMismatchError",
    "GlossaryNotFoundError",
    "LanceDBLexiconStore",
    "_row_from_entry",
]
