"""Tests for the new LanceDB-backed lexicon store (Phase 1 of the migration).

Acceptance: reading from `LexiconStore` returns equivalent results to the
legacy read path (round-trip test) AND all existing API routes still pass.

These tests cover:

- The ``LexiconStore`` Protocol is satisfied by ``LanceDBLexiconStore``.
- ``save_glossary`` + ``get_glossary`` + ``list_glossaries`` round-trip.
- ``toggle_glossary`` flips the ``enabled`` flag everywhere.
- ``reorder_glossaries`` rewrites priorities.
- ``delete_glossary`` removes all rows for a glossary.
- ``hybrid_query`` returns semantically relevant results.
- ``exact_lookup`` matches case-insensitively.
- ``merged_enabled_glossary`` and ``preview`` composition helpers work.
- ``health()`` returns the expected metadata.
- The Protocol/structural-typing check passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("lancedb")

from omniscribe.core.lexicon import (
    LanceDBLexiconStore,
    LexiconHit,
    LexiconQuery,
    LexiconStore,
    merged_enabled_glossary,
    preview,
)
from omniscribe.core.lexicon.embedding import (
    EMBEDDING_DIM,
    EmbeddingModel,
)
from omniscribe.core.translate.glossary import Glossary

# ---------------------------------------------------------------------------
# Test doubles — a tiny deterministic embedding model for fast tests.
# ---------------------------------------------------------------------------


class _FakeEmbeddingModel:
    """Deterministic, hash-based fake embedding model.

    Maps each text to a 384-dim unit vector derived from the text hash. Two
    texts with the same source produce the same vector; similar prefixes
    produce somewhat-similar vectors (hash-bucketed). Not a real semantic
    model — just enough surface for the store to exercise vector search.
    """

    dim = EMBEDDING_DIM
    model_name = "fake-test-model"

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import hashlib

        vectors: list[list[float]] = []
        for t in texts:
            digest = hashlib.sha256(t.encode("utf-8")).digest()
            # Stretch the 32 bytes into 384 dims by repeating + position-mixing.
            base = [b / 255.0 for b in digest] * 12
            vec = base[:EMBEDDING_DIM]
            # Normalize to unit length
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            vec = [x / norm for x in vec]
            vectors.append(vec)
        return vectors


@pytest.fixture
def fake_model() -> EmbeddingModel:
    return _FakeEmbeddingModel()


@pytest.fixture
def store(tmp_path: Path, fake_model: EmbeddingModel) -> LanceDBLexiconStore:
    artifact = tmp_path / "lexicon.lance"
    return LanceDBLexiconStore(path=artifact, embedding_model=fake_model)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_lancedb_store_satisfies_protocol(store: LanceDBLexiconStore) -> None:
    """The concrete implementation must satisfy the runtime-checkable Protocol."""
    assert isinstance(store, LexiconStore)


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_save_and_get_glossary(store: LanceDBLexiconStore) -> None:
    meta = store.save_glossary(
        name="Legal EN→FR",
        format="csv",
        entries=[
            {"source": "agreement", "target": "accord"},
            {"source": "contract", "target": "contrat"},
            {"source": "liability", "target": "responsabilité"},
        ],
        source_uri="inline",
        encoding="utf-8",
        group="legal",
        priority=10,
    )
    assert meta.id
    assert meta.name == "Legal EN→FR"
    assert meta.entry_count == 3
    assert meta.group == "legal"
    assert meta.priority == 10
    assert meta.enabled is True

    fetched = store.get_glossary(meta.id)
    assert fetched is not None
    assert fetched.id == meta.id
    assert fetched.entry_count == 3
    assert fetched.name == "Legal EN→FR"


def test_list_glossaries_sorted_by_priority(store: LanceDBLexiconStore) -> None:
    low = store.save_glossary(
        name="Low",
        format="json_pairs",
        entries=[{"source": "a", "target": "A"}],
        priority=0,
    )
    high = store.save_glossary(
        name="High",
        format="json_pairs",
        entries=[{"source": "b", "target": "B"}],
        priority=100,
    )
    mid = store.save_glossary(
        name="Mid",
        format="json_pairs",
        entries=[{"source": "c", "target": "C"}],
        priority=50,
    )
    metas = store.list_glossaries()
    assert [m.id for m in metas] == [high.id, mid.id, low.id]


def test_save_rejects_empty_name(store: LanceDBLexiconStore) -> None:
    with pytest.raises(ValueError, match="name is required"):
        store.save_glossary(
            name="   ", format="json_pairs", entries=[{"source": "a", "target": "A"}]
        )


def test_save_rejects_empty_entries(store: LanceDBLexiconStore) -> None:
    with pytest.raises(ValueError, match="at least one valid entry"):
        store.save_glossary(name="Empty", format="json_pairs", entries=[])


def test_save_rejects_invalid_priority(store: LanceDBLexiconStore) -> None:
    with pytest.raises(ValueError, match="priority must be an integer"):
        store.save_glossary(
            name="X",
            format="json_pairs",
            entries=[{"source": "a", "target": "A"}],
            priority="not-a-number",  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Toggle / reorder / delete
# ---------------------------------------------------------------------------


def test_toggle_glossary(store: LanceDBLexiconStore) -> None:
    meta = store.save_glossary(
        name="Toggle me", format="json_pairs", entries=[{"source": "a", "target": "A"}]
    )
    assert store.get_glossary(meta.id).enabled is True  # type: ignore[union-attr]
    updated = store.toggle_glossary(meta.id, enabled=False)
    assert updated.enabled is False
    # Round-trip back
    updated2 = store.toggle_glossary(meta.id, enabled=True)
    assert updated2.enabled is True


def test_toggle_missing_glossary_raises(store: LanceDBLexiconStore) -> None:
    with pytest.raises(KeyError):
        store.toggle_glossary("nonexistent", enabled=False)


def test_reorder_glossaries(store: LanceDBLexiconStore) -> None:
    a = store.save_glossary(
        name="A",
        format="json_pairs",
        entries=[{"source": "a", "target": "A"}],
        priority=0,
    )
    b = store.save_glossary(
        name="B",
        format="json_pairs",
        entries=[{"source": "b", "target": "B"}],
        priority=0,
    )
    c = store.save_glossary(
        name="C",
        format="json_pairs",
        entries=[{"source": "c", "target": "C"}],
        priority=0,
    )
    # Order: c first (highest priority), a second, b last
    store.reorder_glossaries([c.id, a.id, b.id])
    metas = store.list_glossaries()
    assert [m.id for m in metas] == [c.id, a.id, b.id]


def test_reorder_unknown_id_raises(store: LanceDBLexiconStore) -> None:
    a = store.save_glossary(
        name="A", format="json_pairs", entries=[{"source": "a", "target": "A"}]
    )
    with pytest.raises(KeyError):
        store.reorder_glossaries([a.id, "bogus"])


def test_delete_glossary(store: LanceDBLexiconStore) -> None:
    a = store.save_glossary(
        name="A", format="json_pairs", entries=[{"source": "a", "target": "A"}]
    )
    b = store.save_glossary(
        name="B", format="json_pairs", entries=[{"source": "b", "target": "B"}]
    )
    assert store.delete_glossary(a.id) is True
    assert store.get_glossary(a.id) is None
    # The other glossary is untouched
    assert store.get_glossary(b.id) is not None
    # Deleting again is a no-op
    assert store.delete_glossary(a.id) is False


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def test_hybrid_query_returns_relevant_hits(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="Animals",
        format="json_pairs",
        entries=[
            {"source": "dog", "target": "perro"},
            {"source": "cat", "target": "gato"},
            {"source": "bird", "target": "pájaro"},
        ],
    )
    # Use a near-identical source for the query so the fake embedding model
    # returns a vector close to the matched term.
    hits = store.hybrid_query(LexiconQuery(source_chunk="dog", limit=2))
    assert len(hits) >= 1
    assert all(isinstance(h, LexiconHit) for h in hits)
    # Top hit should be the exact "dog" entry
    assert hits[0].entry.source_text == "dog"
    assert hits[0].entry.target_text == "perro"


def test_hybrid_query_filters_disabled(store: LanceDBLexiconStore) -> None:
    meta = store.save_glossary(
        name="Animals",
        format="json_pairs",
        entries=[{"source": "dog", "target": "perro"}],
    )
    store.toggle_glossary(meta.id, enabled=False)
    # With enabled_only=True (default), the disabled glossary's terms are skipped
    hits = store.hybrid_query(LexiconQuery(source_chunk="dog", limit=5))
    assert all(h.entry.glossary_id != meta.id for h in hits)
    # With enabled_only=False, they show up again
    hits2 = store.hybrid_query(
        LexiconQuery(source_chunk="dog", limit=5, enabled_only=False)
    )
    assert any(h.entry.glossary_id == meta.id for h in hits2)


def test_hybrid_query_filters_by_glossary_ids(store: LanceDBLexiconStore) -> None:
    g1 = store.save_glossary(
        name="G1", format="json_pairs", entries=[{"source": "a", "target": "A"}]
    )
    g2 = store.save_glossary(
        name="G2", format="json_pairs", entries=[{"source": "a", "target": "A2"}]
    )
    _ = g2  # silence F841 — referenced only to keep the glossary alive
    hits = store.hybrid_query(
        LexiconQuery(source_chunk="a", limit=10, glossary_ids=[g1.id])
    )
    assert all(h.entry.glossary_id == g1.id for h in hits)


def test_exact_lookup_case_insensitive(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="Mixed",
        format="json_pairs",
        entries=[
            {"source": "Apple", "target": "Pomme"},
            {"source": "Banana", "target": "Banane"},
        ],
    )
    found = store.exact_lookup("APPLE", source_lang="", target_lang="")
    assert len(found) == 1
    assert found[0].target_text == "Pomme"
    found2 = store.exact_lookup("banana", source_lang="", target_lang="")
    assert len(found2) == 1
    assert found2[0].target_text == "Banane"


def test_list_entries(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="Numbers",
        format="json_pairs",
        entries=[
            {"source": "one", "target": "un"},
            {"source": "two", "target": "deux"},
            {"source": "three", "target": "trois"},
        ],
    )
    metas = store.list_glossaries()
    entries = store.list_entries(metas[0].id)
    assert len(entries) == 3
    assert {e.source_text for e in entries} == {"one", "two", "three"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_returns_expected_keys(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="G", format="json_pairs", entries=[{"source": "a", "target": "A"}]
    )
    h = store.health()
    assert h["row_count"] == 1
    assert h["glossary_count"] == 1
    assert h["embedding_dim"] == EMBEDDING_DIM
    assert h["embedding_model"] == "fake-test-model"
    assert "path" in h
    assert "index_spec" in h


# ---------------------------------------------------------------------------
# Composition helpers
# ---------------------------------------------------------------------------


def test_merged_enabled_glossary_helper(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="Low",
        format="json_pairs",
        entries=[{"source": "cat", "target": "gato"}],
        priority=0,
    )
    store.save_glossary(
        name="High",
        format="json_pairs",
        entries=[{"source": "cat", "target": "chat"}],  # overrides Low
        priority=10,
    )
    merged = merged_enabled_glossary(store)
    assert isinstance(merged, Glossary)
    cats = [e for e in merged.entries if e.source == "cat"]
    assert len(cats) == 1
    # Higher-priority glossary wins
    assert cats[0].target == "chat"


def test_preview_helper(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="A",
        format="json_pairs",
        entries=[{"source": "bank", "target": "banque"}],
    )
    store.save_glossary(
        name="B",
        format="json_pairs",
        entries=[{"source": "bank", "target": "rive"}],
    )
    p = preview(store)
    assert p["count"] == 1
    assert len(p["conflicts"]) == 1  # type: ignore[arg-type]
    conflict = p["conflicts"][0]  # type: ignore[index]
    assert conflict["source"] == "bank"
    assert set(conflict["targets"]) == {"banque", "rive"}


# ---------------------------------------------------------------------------
# Persistence — re-open the store and confirm data survives.
# ---------------------------------------------------------------------------


def test_persistence_across_instances(
    tmp_path: Path, fake_model: EmbeddingModel
) -> None:
    artifact = tmp_path / "lexicon.lance"
    s1 = LanceDBLexiconStore(path=artifact, embedding_model=fake_model)
    saved = s1.save_glossary(
        name="Persisted",
        format="json_pairs",
        entries=[{"source": "hi", "target": "salut"}],
    )
    s1.close()

    s2 = LanceDBLexiconStore(path=artifact, embedding_model=fake_model)
    metas = s2.list_glossaries()
    assert len(metas) == 1
    assert metas[0].id == saved.id
    assert metas[0].name == "Persisted"
    entries = s2.list_entries(saved.id)
    assert entries[0].source_text == "hi"
    assert entries[0].target_text == "salut"


# ---------------------------------------------------------------------------
# normalize_term / entry_hash primitives (LLM-remediation wave)
# ---------------------------------------------------------------------------


def test_normalize_term_casefold_and_nfc() -> None:
    from omniscribe.core.lexicon.store import normalize_term

    assert normalize_term("  Straße ") == "strasse"
    # NFD combining acute + e composes to the same key as precomposed é.
    assert normalize_term("e\u0301tude") == normalize_term("étude")
    assert normalize_term("") == ""


def test_entry_hash_stable_and_case_sensitive() -> None:
    from omniscribe.core.lexicon.store import entry_hash

    assert entry_hash("EU", "UE") == entry_hash("EU", "UE")
    assert entry_hash("EU", "UE") != entry_hash("eu", "ue")
    assert entry_hash("EU", "UE") != entry_hash("EU", "union européenne")


# ---------------------------------------------------------------------------
# Model guard + legacy-column backfill (LLM-remediation wave)
# ---------------------------------------------------------------------------


def test_reopen_with_different_model_raises(tmp_path: Path) -> None:
    """Opening a lexicon built with model A using model B must fail loud."""
    from omniscribe.core.lexicon.lancedb_store import (
        EmbeddingModelMismatchError,
        LanceDBLexiconStore,
    )

    from fake_embedder import HashEmbedder

    store = LanceDBLexiconStore(path=tmp_path, embedding_model=HashEmbedder("model-a"))
    store.save_glossary(
        name="g", format="csv", entries=[{"source": "a", "target": "b"}]
    )
    store.close()
    with pytest.raises(EmbeddingModelMismatchError, match="model-a"):
        LanceDBLexiconStore(
            path=tmp_path, embedding_model=HashEmbedder("model-b")
        ).health()


def test_reopen_with_same_model_ok(tmp_path: Path) -> None:
    from omniscribe.core.lexicon.lancedb_store import LanceDBLexiconStore

    from fake_embedder import HashEmbedder

    store = LanceDBLexiconStore(path=tmp_path, embedding_model=HashEmbedder("model-a"))
    store.save_glossary(
        name="g", format="csv", entries=[{"source": "a", "target": "b"}]
    )
    store.close()
    reopened = LanceDBLexiconStore(
        path=tmp_path, embedding_model=HashEmbedder("model-a")
    )
    assert reopened.list_glossaries()


def test_legacy_table_without_meta_adopts_current_model(tmp_path: Path) -> None:
    """A pre-``_meta`` lexicon opens fine and records the current model."""
    import lancedb

    from omniscribe.core.lexicon.lancedb_store import LanceDBLexiconStore
    from omniscribe.core.lexicon.schema import LEXICON_SCHEMA

    from fake_embedder import HashEmbedder

    # Simulate a legacy lexicon: terms table only, no _meta table.
    db = lancedb.connect(str(tmp_path))
    db.create_table("terms", schema=LEXICON_SCHEMA, mode="create")
    del db

    store = LanceDBLexiconStore(
        path=tmp_path, embedding_model=HashEmbedder("legacy-adopt")
    )
    # Opening must not raise; _meta now exists with the adopted model.
    assert store.health()["embedding_model"] == "legacy-adopt"
    store.close()
    # And a mismatched reopen is now guarded.
    from omniscribe.core.lexicon.lancedb_store import EmbeddingModelMismatchError

    with pytest.raises(EmbeddingModelMismatchError):
        LanceDBLexiconStore(
            path=tmp_path, embedding_model=HashEmbedder("other-model")
        ).health()


def test_legacy_table_without_entry_hash_gets_backfilled(tmp_path: Path) -> None:
    """A pre-``entry_hash`` table adopts the column and accepts new rows."""
    import lancedb
    import pyarrow as pa

    from omniscribe.core.lexicon.lancedb_store import LanceDBLexiconStore
    from omniscribe.core.lexicon.schema import LEXICON_SCHEMA

    from fake_embedder import HashEmbedder

    legacy_fields = [f for f in LEXICON_SCHEMA if f.name != "entry_hash"]
    legacy_schema = pa.schema(legacy_fields)

    db = lancedb.connect(str(tmp_path))
    db.create_table("terms", schema=legacy_schema, mode="create")
    del db

    store = LanceDBLexiconStore(path=tmp_path, embedding_model=HashEmbedder("m"))
    meta = store.save_glossary(
        name="g", format="csv", entries=[{"source": "x", "target": "y"}]
    )
    entries = store.list_entries(meta.id)
    assert entries[0].source_text == "x"
    store.close()


# ---------------------------------------------------------------------------
# Hybrid search: keyword leg + RRF fusion (LLM-remediation wave)
# ---------------------------------------------------------------------------


def test_hybrid_rff_surfaces_exact_acronym(store: LanceDBLexiconStore) -> None:
    """An acronym exact-match must outrank a keyword-less vector hit."""
    store.save_glossary(
        name="g",
        format="csv",
        entries=[
            {"source": "GDPR", "target": "RGPD"},
            {"source": "privacy regulation", "target": "règlement"},
        ],
    )
    hits = store.hybrid_query(LexiconQuery(source_chunk="GDPR compliance", limit=2))
    assert hits, "expected at least one hit"
    assert hits[0].entry.source_text == "GDPR"
    assert hits[0].keyword_score > 0.0  # type: ignore[typeddict-item]


def test_keyword_only_match_survives_low_cosine(store: LanceDBLexiconStore) -> None:
    """A keyword-exact entry must not be dropped by a strict cosine floor."""
    store.save_glossary(
        name="g", format="csv", entries=[{"source": "XK-942", "target": "XK-942-B"}]
    )
    hits = store.hybrid_query(
        LexiconQuery(source_chunk="ref XK-942 unit", limit=3, min_score=0.9)
    )
    assert [h.entry.source_text for h in hits] == ["XK-942"]


def test_min_score_drops_weak_vector_only_hits(store: LanceDBLexiconStore) -> None:
    store.save_glossary(
        name="g",
        format="csv",
        entries=[{"source": "unrelated term", "target": "terme sans rapport"}],
    )
    hits = store.hybrid_query(
        LexiconQuery(source_chunk="completely other words", limit=3, min_score=0.99)
    )
    assert hits == []


def test_fingerprint_stable_until_mutation(store: LanceDBLexiconStore) -> None:
    fp1 = store.fingerprint()
    assert store.fingerprint() == fp1
    store.save_glossary(
        name="g", format="csv", entries=[{"source": "a", "target": "A"}]
    )
    assert store.fingerprint() != fp1
    meta = store.get_glossary(store.list_glossaries()[0].id)
    assert meta is not None
    store.toggle_glossary(meta.id, enabled=False)
    assert store.fingerprint() != fp1


def test_hybrid_query_logs_hits_debug(
    store: LanceDBLexiconStore, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    store.save_glossary(
        name="g", format="csv", entries=[{"source": "dog", "target": "perro"}]
    )
    with caplog.at_level(logging.DEBUG, logger="omniscribe.core.lexicon.lancedb_store"):
        store.hybrid_query(LexiconQuery(source_chunk="dog", limit=3))
    assert any("lexicon query" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Vector index creation (LLM-remediation wave)
# ---------------------------------------------------------------------------


def test_ensure_index_called_after_bulk_save(
    store: LanceDBLexiconStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save_glossary must drive idempotent index creation (>=128 rows)."""
    calls: list[dict] = []
    store._ensure_open()
    real_create = store._table.create_index

    def spy(**kwargs):
        calls.append(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(store._table, "create_index", spy, raising=False)
    store.save_glossary(
        name="bulk",
        format="csv",
        entries=[
            {"source": f"term {i}", "target": f"terme {i}"} for i in range(200)
        ],
    )
    assert calls, "create_index was never called"
    assert calls[0]["index_type"] in {"hnsw", "ivf_pq"}
    assert calls[0]["vector_column_name"] == "embedding"


def test_index_skipped_below_threshold(
    store: LanceDBLexiconStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []
    store._ensure_open()
    monkeypatch.setattr(
        store._table,
        "create_index",
        lambda **kw: calls.append(kw),
        raising=False,
    )
    store.save_glossary(
        name="small", format="csv", entries=[{"source": "a", "target": "A"}]
    )
    assert calls == []
    health = store.health()
    assert "index_status" in health


# ---------------------------------------------------------------------------
# Upsert on re-import + embedding reuse (LLM-remediation wave)
# ---------------------------------------------------------------------------


def test_save_glossary_upsert_replaces_same_name_and_uri(
    store: LanceDBLexiconStore,
) -> None:
    first = store.save_glossary(
        name="eu",
        format="csv",
        source_uri="file://a.csv",
        entries=[{"source": "EU", "target": "UE"}],
    )
    second = store.save_glossary(
        name="eu",
        format="csv",
        source_uri="file://a.csv",
        upsert=True,
        entries=[{"source": "EU", "target": "Union européenne"}],
    )
    assert second.id == first.id
    assert len(store.list_glossaries()) == 1
    assert [e.target_text for e in store.list_entries(first.id)] == [
        "Union européenne"
    ]


def test_save_glossary_upsert_different_uri_creates_new(
    store: LanceDBLexiconStore,
) -> None:
    store.save_glossary(
        name="g", format="csv", source_uri="file://a.csv",
        entries=[{"source": "a", "target": "A"}],
    )
    second = store.save_glossary(
        name="g", format="csv", source_uri="file://b.csv", upsert=True,
        entries=[{"source": "b", "target": "B"}],
    )
    metas = store.list_glossaries()
    assert len(metas) == 2
    assert second.id != metas[0].id or second.id != metas[1].id


def test_save_glossary_explicit_glossary_id_keeps_id(
    store: LanceDBLexiconStore,
) -> None:
    meta = store.save_glossary(
        name="g",
        format="csv",
        glossary_id="legacy-123",
        entries=[{"source": "a", "target": "b"}],
    )
    assert meta.id == "legacy-123"
    store.save_glossary(
        name="g",
        format="csv",
        glossary_id="legacy-123",
        entries=[{"source": "c", "target": "D"}],
    )
    assert len(store.list_glossaries()) == 1
    assert [e.source_text for e in store.list_entries("legacy-123")] == ["c"]


def test_reimport_reuses_embeddings_for_unchanged_entries(tmp_path: Path) -> None:
    from fake_embedder import HashEmbedder

    model = HashEmbedder("reuse-model")
    store = LanceDBLexiconStore(path=tmp_path, embedding_model=model)
    store.save_glossary(
        name="g", format="csv", entries=[{"source": "a", "target": "b"}]
    )
    calls = {"n": 0}
    real_batch = model.embed_batch

    def counting_batch(texts: list[str]) -> list[list[float]]:
        calls["n"] += len(texts)
        return real_batch(texts)

    model.embed_batch = counting_batch  # type: ignore[method-assign]
    store.save_glossary(
        name="g",
        format="csv",
        upsert=True,
        entries=[{"source": "a", "target": "b"}, {"source": "c", "target": "d"}],
    )
    assert calls["n"] == 1  # only the new entry was embedded
