"""Regression tests for projection push-down in the hybrid search engine.

The lexicon search paths used to materialise every column of a LanceDB result
— including the 384-float ``embedding`` — into Python objects and only then
discard the embedding. These tests pin the projection to the query itself.

They use hand-written stubs rather than a real LanceDB table so they run
without the optional ``pyarrow`` / ``lancedb`` extras installed.
"""

from __future__ import annotations

from typing import Any

import pytest

from omniscribe.core.lexicon.search import HybridSearchEngine
from omniscribe.core.lexicon.store import LexiconQuery

_EMBEDDING = [0.01] * 384


class _FakeArrow:
    """Minimal stand-in for ``pa.Table`` restricted to what the engine uses."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    @property
    def column_names(self) -> list[str]:
        return list(self._rows[0]) if self._rows else []

    def select(self, columns: list[str]) -> _FakeArrow:
        return _FakeArrow([{c: r[c] for c in columns if c in r} for r in self._rows])

    def to_pylist(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class _FakeQuery:
    """Fake LanceDB query builder honouring the projection semantics.

    ``to_arrow()`` returns only the projected columns (plus ``_distance`` for a
    vector query, which LanceDB always appends), mirroring real behaviour. A
    projection applied after ``to_arrow()`` therefore cannot recover a column
    the query already dropped.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        vector: bool = False,
        log: dict[str, Any] | None = None,
    ) -> None:
        self._rows = rows
        self._vector = vector
        self._columns: list[str] | None = None
        self._log = log if log is not None else {}

    def _apply(self, columns: list[str]) -> _FakeQuery:
        missing = [c for c in columns if c not in self._all_names()]
        if missing:
            raise ValueError(f"invalid column {missing}")
        self._columns = columns
        return self

    def _all_names(self) -> list[str]:
        return list(self._rows[0]) if self._rows else []

    def metric(self, _metric: str) -> _FakeQuery:
        return self

    def distance_type(self, _metric: str) -> _FakeQuery:
        return self

    def limit(self, _limit: int) -> _FakeQuery:
        return self

    def where(self, _where: str, prefilter: bool = True) -> _FakeQuery:
        return self

    def select(self, columns: list[str]) -> _FakeQuery:
        self._log["select"] = list(columns)
        return self._apply(columns)

    def to_arrow(self) -> _FakeArrow:
        self._log["select_at_materialize"] = self._columns
        if self._columns is None:
            return _FakeArrow(self._rows)
        rows: list[dict[str, Any]] = []
        for row in self._rows:
            out = {c: row[c] for c in self._columns if c in row}
            if self._vector and "_distance" not in out:
                out["_distance"] = row.get("_distance", 0.0)
            rows.append(out)
        return _FakeArrow(rows)


class _FakeTable:
    """Fake LanceDB table exposing only the attributes the engine touches."""

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        vector: bool = False,
        log: dict[str, Any] | None = None,
    ) -> None:
        self._rows = rows
        self._vector = vector
        self._log = log if log is not None else {}

    @property
    def schema(self) -> Any:
        return type("S", (), {"names": list(self._rows[0])})()

    def search(self, *_args: Any, **_kwargs: Any) -> _FakeQuery:
        return _FakeQuery(self._rows, vector=self._vector, log=self._log)


def _entry_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": "a",
            "source_text": "alpha",
            "glossary_id": "g1",
            "embedding": list(_EMBEDDING),
            "_distance": 0.1,
        },
        {
            "id": "b",
            "source_text": "beta",
            "glossary_id": "g1",
            "embedding": list(_EMBEDDING),
            "_distance": 0.2,
        },
    ]


def test_vector_search_pushes_projection_into_query() -> None:
    """The vector leg must not materialise the embedding column."""
    log: dict[str, Any] = {}
    table = _FakeTable(_entry_rows(), vector=True, log=log)

    scores = HybridSearchEngine()._execute_vector_search(table, [[0.1, 0.2]], None, 5)

    assert scores is not None
    assert set(scores) == {"a", "b"}
    assert scores["a"] == pytest.approx(0.9)
    assert scores["b"] == pytest.approx(0.8)
    assert log["select"] == ["id", "_distance"]
    assert log["select_at_materialize"] == ["id", "_distance"]
    assert "embedding" not in log["select_at_materialize"]


def test_vector_search_scores_match_unprojected_rows() -> None:
    """Scoring must be unchanged when the projection is applied."""
    rows = _entry_rows()
    projected = HybridSearchEngine()._execute_vector_search(
        _FakeTable(rows, vector=True), [[0.1, 0.2]], None, 5
    )

    expected: dict[str, float] = {}
    for row in rows:
        score = max(0.0, min(1.0, 1.0 - float(row.get("_distance", 1.0))))
        expected[str(row.get("id"))] = max(score, expected.get(str(row.get("id")), 0.0))
    assert projected == expected


def test_keyword_search_pushes_projection_into_query() -> None:
    """The keyword leg must project in the query, not after materialisation."""
    log: dict[str, Any] = {}
    table = _FakeTable(_entry_rows(), log=log)
    engine = HybridSearchEngine()

    scores = engine.keyword_scores(table, LexiconQuery(source_chunk="alpha"))

    assert scores == {"a": 1.0}
    assert log["select_at_materialize"] is not None
    assert "source_text" in log["select_at_materialize"]
    assert "embedding" not in log["select_at_materialize"]


def test_keyword_search_projection_matches_column_intersection() -> None:
    """Projected columns equal the keyword projection present in the schema."""
    rows = _entry_rows()
    engine = HybridSearchEngine()
    table = _FakeTable(rows)
    query = LexiconQuery(source_chunk="alpha")

    projected_scores = engine.keyword_scores(table, query)

    # Previous behaviour: materialise everything, then intersect post-hoc.
    tbl = _FakeArrow(rows)
    cols = [c for c in engine._KEYWORD_PROJECTION if c in tbl.column_names]
    legacy_rows = tbl.select(cols).to_pylist()
    legacy: dict[str, float] = {}
    for row in legacy_rows:
        source_norm = str(row.get("source_text", "")).lower()
        if source_norm == "alpha":
            legacy[str(row.get("id"))] = max(legacy.get(str(row.get("id")), 0.0), 1.0)

    assert projected_scores == legacy


class _NoSchemaTable(_FakeTable):
    """Fake table whose schema cannot be read."""

    @property
    def schema(self) -> Any:
        raise RuntimeError("schema unavailable")


def test_keyword_search_without_schema_still_scans() -> None:
    """A table without a readable schema falls back to an unrestricted scan."""
    log: dict[str, Any] = {}
    table = _NoSchemaTable(_entry_rows(), log=log)

    scores = HybridSearchEngine().keyword_scores(
        table, LexiconQuery(source_chunk="alpha")
    )

    assert scores == {"a": 1.0}
    assert log["select_at_materialize"] is None
