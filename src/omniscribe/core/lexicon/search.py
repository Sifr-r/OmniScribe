"""Hybrid (vector + keyword) search engine for LanceDB lexicon stores.

Combines cosine ANN vector retrieval with deterministic normalized keyword
matching fused via Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, ClassVar

import numpy as np

from .embedding import EmbeddingModel
from .lancedb_helpers import _entry_from_row, _sql_escape
from .query_terms import candidate_terms
from .store import LexiconEntry, LexiconHit, LexiconQuery, normalize_term

logger = logging.getLogger("omniscribe.core.lexicon.lancedb_store")


class HybridSearchEngine:
    """Executes hybrid vector + keyword searches and exact lookups over LanceDB tables."""

    RRF_K: int = 60
    _KEYWORD_PROJECTION: ClassVar[list[str]] = [
        "id",
        "glossary_id",
        "source_text",
        "target_text",
        "source_lang",
        "target_lang",
        "domain",
        "register",
        "pos",
        "case_sensitive",
        "notes",
        "source_uri",
        "source_format",
        "usage_count",
        "entry_hash",
        "created_at",
        "updated_at",
        "glossary_name",
        "glossary_enabled",
        "glossary_priority",
        "glossary_group",
        "glossary_source_uri",
        "glossary_encoding",
    ]

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None or not raw.strip():
            return default
        try:
            return float(raw)
        except ValueError:
            logger.warning("Env %s=%r invalid; using default %s", name, raw, default)
            return default

    def build_where(self, query: LexiconQuery) -> str | None:
        """Build a LanceDB WHERE clause string from structured query filters."""
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

    def matches_query(self, row: dict[str, Any], query: LexiconQuery) -> bool:
        """Evaluate query filter predicates against an in-memory row dict."""
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

    def _embed_query_texts(
        self,
        query: LexiconQuery,
        terms: list[str],
        embedding_model: EmbeddingModel,
    ) -> list[list[float]]:
        """Batch-embed the truncated source chunk and candidate terms."""
        query_texts = [query.source_chunk[:512], *terms[:4]]
        if len(query.source_chunk) > 512:
            logger.warning(
                "Lexicon query chunk is %d chars; truncated to 512 for the "
                "embedding window (the keyword leg still sees the full chunk).",
                len(query.source_chunk),
            )
        try:
            return embedding_model.embed_batch(query_texts)
        except Exception:
            return [embedding_model.embed(t) for t in query_texts]

    def _execute_vector_search(
        self,
        table: Any,
        query_vecs: list[list[float]],
        where_clauses: str | None,
        over: int,
    ) -> dict[str, float] | None:
        """Execute LanceDB ANN search for each vector, taking max score per row id."""
        vector_scores: dict[str, float] = {}
        for vec in query_vecs:
            if not vec or not any(vec):
                continue
            try:
                search = (
                    table.search(vec, vector_column_name="embedding")
                    .metric("cosine")
                    .limit(over)
                )
                if where_clauses:
                    search = search.where(where_clauses, prefilter=True)
                raw = search.to_arrow().to_pylist()
            except Exception as exc:
                logger.warning(
                    "LanceDB vector search failed: %s; falling back to Arrow search",
                    exc,
                )
                return None
            for row in raw:
                row_id = str(row.get("id"))
                score = max(0.0, min(1.0, 1.0 - float(row.get("_distance", 1.0))))
                if score > vector_scores.get(row_id, 0.0):
                    vector_scores[row_id] = score
        return vector_scores

    def _filter_hits(
        self,
        fused: list[tuple[str, float]],
        rows_by_id: dict[str, dict[str, Any]],
        vector_scores: dict[str, float],
        keyword_scores: dict[str, float],
        min_score: float,
        limit: int,
    ) -> list[LexiconHit]:
        """Convert fused rankings into LexiconHit objects applying thresholds."""
        hits: list[LexiconHit] = []
        for gid, _rrf in fused:
            row = rows_by_id.get(gid)
            if row is None:
                continue
            cos = vector_scores.get(gid, 0.0)
            kw = keyword_scores.get(gid, 0.0)
            if cos < min_score and kw < 0.8:
                continue
            hits.append(
                LexiconHit(entry=_entry_from_row(row), score=cos, keyword_score=kw)
            )
            if len(hits) >= limit:
                break
        return hits

    def hybrid_search(
        self,
        table: Any,
        query: LexiconQuery,
        embedding_model: EmbeddingModel,
    ) -> list[LexiconHit]:
        """Perform full hybrid search over LanceDB table using vector + keyword legs."""
        if not query.source_chunk or not query.source_chunk.strip():
            return []
        try:
            row_count = table.count_rows()
        except Exception:
            row_count = table.to_arrow().num_rows
        if row_count == 0:
            return []

        terms = candidate_terms(query.source_chunk)
        query_vecs = self._embed_query_texts(query, terms, embedding_model)
        if not query_vecs or not any(any(vec) for vec in query_vecs):
            return []

        where_clauses = self.build_where(query)
        over = max(query.limit * 3, 24)
        vector_scores = self._execute_vector_search(
            table, query_vecs, where_clauses, over
        )
        if vector_scores is None:
            return self.hybrid_via_arrow(table, query, embedding_model)

        keyword_scores = self.keyword_scores(table, query)
        fused = self.rrf_fuse(vector_scores, keyword_scores, over)
        if not fused:
            return []

        rows_by_id = self.rows_by_id(table, {gid for gid, _ in fused})
        min_score = query.min_score if query.min_score is not None else 0.0
        hits = self._filter_hits(
            fused, rows_by_id, vector_scores, keyword_scores, min_score, query.limit
        )

        if hits:
            logger.debug(
                "lexicon query terms=%s top=%s",
                terms[:3],
                [
                    (h.entry.source_text, round(h.score, 3), round(h.keyword_score, 2))
                    for h in hits[:3]
                ],
            )
        return hits

    def keyword_scores(self, table: Any, query: LexiconQuery) -> dict[str, float]:
        """Deterministic keyword evidence: exact > prefix > substring."""
        terms = [normalize_term(t) for t in candidate_terms(query.source_chunk)]
        terms.extend(
            normalize_term(w)
            for w in re.findall(r"[^\W_]+", query.source_chunk, re.UNICODE)
            if len(w) >= 3
        )
        chunk_norm = normalize_term(query.source_chunk[:80])
        if chunk_norm:
            terms.append(chunk_norm)
        seen: set[str] = set()
        deduped: list[str] = []
        for term in terms:
            if not term or term in seen:
                continue
            seen.add(term)
            deduped.append(term)
            if len(deduped) >= 24:
                break
        if not deduped:
            return {}
        try:
            search = table.search()
            where = self.build_where(query)
            if where:
                search = search.where(where, prefilter=True)
            tbl = search.to_arrow()
            cols = [c for c in self._KEYWORD_PROJECTION if c in tbl.column_names]
            rows = tbl.select(cols).to_pylist()
        except Exception as exc:
            logger.debug("keyword leg scan failed: %s", exc)
            return {}
        scores: dict[str, float] = {}
        for row in rows:
            source_norm = normalize_term(str(row.get("source_text", "")))
            best = 0.0
            for term in terms:
                if not term:
                    continue
                if source_norm == term:
                    best = max(best, 1.0)
                elif source_norm.startswith(term) or term.startswith(source_norm):
                    best = max(best, 0.8)
                elif term in source_norm:
                    best = max(best, 0.6)
            if best:
                scores[str(row.get("id"))] = best
        return scores

    def rrf_fuse(
        self,
        vector_scores: dict[str, float],
        keyword_scores: dict[str, float],
        depth: int,
    ) -> list[tuple[str, float]]:
        """Reciprocal-rank fusion of vector and keyword scores."""
        vector_weight = self._env_float("OMNISCRIBE_LEXICON_VECTOR_WEIGHT", 0.6)
        keyword_weight = self._env_float("OMNISCRIBE_LEXICON_KEYWORD_WEIGHT", 0.4)
        fused: dict[str, float] = {}
        for rank, (gid, _score) in enumerate(
            sorted(vector_scores.items(), key=lambda kv: -kv[1])[:depth]
        ):
            fused[gid] = fused.get(gid, 0.0) + vector_weight / (self.RRF_K + rank + 1)
        for rank, (gid, _score) in enumerate(
            sorted(keyword_scores.items(), key=lambda kv: -kv[1])[:depth]
        ):
            fused[gid] = fused.get(gid, 0.0) + keyword_weight / (self.RRF_K + rank + 1)
        return sorted(fused.items(), key=lambda kv: -kv[1])

    def rows_by_id(self, table: Any, ids: set[str]) -> dict[str, dict[str, Any]]:
        """Retrieve projection rows for a given set of row IDs."""
        if not ids:
            return {}
        escaped = ", ".join(f"'{_sql_escape(g)}'" for g in ids)
        rows: dict[str, dict[str, Any]] = {}
        try:
            tbl = table.search().where(f"id IN ({escaped})").to_arrow()
            cols = [c for c in self._KEYWORD_PROJECTION if c in tbl.column_names]
            rows = {str(r["id"]): r for r in tbl.select(cols).to_pylist()}
        except Exception:
            try:
                tbl = table.to_arrow()
                cols = [c for c in self._KEYWORD_PROJECTION if c in tbl.column_names]
                for r in tbl.select(cols).to_pylist():
                    if str(r.get("id")) in ids:
                        rows[str(r["id"])] = r
            except Exception:
                return {}
        return rows

    def hybrid_via_arrow(
        self,
        table: Any,
        query: LexiconQuery,
        embedding_model: EmbeddingModel,
    ) -> list[LexiconHit]:
        """Fallback in-Python vector search via PyArrow and NumPy."""
        try:
            where = self.build_where(query)
            tbl = table.search().where(where).to_arrow() if where else table.to_arrow()
        except Exception:
            try:
                tbl = table.to_arrow()
            except Exception:
                return []
        if tbl.num_rows == 0:
            return []

        rows = tbl.to_pylist()
        candidates = [r for r in rows if self.matches_query(r, query)]
        if not candidates:
            return []

        query_vec = np.asarray(
            embedding_model.embed(query.source_chunk), dtype=np.float32
        )
        emb_matrix = np.asarray([r["embedding"] for r in candidates], dtype=np.float32)
        qn = query_vec / (np.linalg.norm(query_vec) + 1e-12)
        en = emb_matrix / (np.linalg.norm(emb_matrix, axis=1, keepdims=True) + 1e-12)
        scores = en @ qn
        order = np.argsort(-scores)
        hits: list[LexiconHit] = []
        for idx in order:
            score = max(0.0, min(1.0, float(scores[idx])))
            if query.min_score is not None and score < query.min_score:
                continue
            row = candidates[int(idx)]
            hits.append(
                LexiconHit(entry=_entry_from_row(row), score=score, keyword_score=0.0)
            )
            if len(hits) >= query.limit:
                break
        return hits

    def exact_lookup(
        self,
        table: Any,
        source_text: str,
        *,
        source_lang: str,
        target_lang: str,
    ) -> list[LexiconEntry]:
        """Exact lookup for a term probe matching language pair."""
        probe = source_text.strip()
        if not probe:
            return []
        probe_norm = normalize_term(probe)
        where_parts: list[str] = []
        if source_lang:
            where_parts.append(f"source_lang = '{_sql_escape(source_lang)}'")
        if target_lang:
            where_parts.append(f"target_lang = '{_sql_escape(target_lang)}'")
        try:
            search = table.search()
            if where_parts:
                search = search.where(" AND ".join(where_parts))
            tbl = search.to_arrow()
        except Exception:
            tbl = table.to_arrow()
        if tbl.num_rows == 0:
            return []

        entries: list[LexiconEntry] = []
        for row in tbl.to_pylist():
            row_source = str(row.get("source_text", "")).strip()
            if bool(row.get("case_sensitive", False)):
                if row_source != probe:
                    continue
            elif normalize_term(row_source) != probe_norm:
                continue
            if source_lang and str(row.get("source_lang", "")) != source_lang:
                continue
            if target_lang and str(row.get("target_lang", "")) != target_lang:
                continue
            entries.append(_entry_from_row(row))
        return entries

    def list_entries(self, table: Any, glossary_id: str) -> list[LexiconEntry]:
        """List all entries belonging to a specific glossary ID."""
        target = str(glossary_id)
        escaped_target = _sql_escape(target)
        try:
            search = table.search().where(f"glossary_id = '{escaped_target}'")
            tbl = search.to_arrow()
        except Exception:
            all_tbl = table.to_arrow()
            if all_tbl.num_rows == 0:
                return []
            import pyarrow.compute as pc

            mask = pc.equal(all_tbl["glossary_id"], target)
            tbl = all_tbl.filter(mask)
        if tbl.num_rows == 0:
            return []
        return [_entry_from_row(r) for r in tbl.to_pylist()]


__all__ = [
    "HybridSearchEngine",
]
