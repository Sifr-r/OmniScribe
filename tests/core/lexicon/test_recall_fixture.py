"""Deterministic retrieval-quality fixture (recall@k) for the hybrid query.

Uses a mapped fake embedder (term → orthogonal unit vector, unknown terms
→ zero vector) so retrieval outcomes are exactly predictable — this is a
regression fixture for the RRF hybrid rewrite, not a benchmark.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("lancedb")

from omniscribe.core.lexicon import LanceDBLexiconStore, LexiconQuery

from fake_embedder import MappedEmbedder, unit_vector


def _store(tmp_path: Path) -> LanceDBLexiconStore:
    mapping = {
        "privacy": unit_vector(0),
        "règlement": unit_vector(1),
        "données": unit_vector(2),
        "GDPR": unit_vector(3),
    }
    store = LanceDBLexiconStore(
        path=tmp_path, embedding_model=MappedEmbedder(mapping)
    )
    store.save_glossary(
        name="terms",
        format="csv",
        entries=[
            {"source": "privacy", "target": "confidentialité"},
            {"source": "règlement", "target": "règlement intérieur"},
            {"source": "GDPR", "target": "RGPD"},
            {"source": "données", "target": "les données"},
            {"source": "unrelated", "target": "sans rapport"},
        ],
    )
    return store


def test_recall_at_3_hybrid(tmp_path: Path) -> None:
    """Known-relevant entries must appear in top-3 for a mixed query."""
    store = _store(tmp_path)
    hits = store.hybrid_query(
        LexiconQuery(source_chunk="GDPR privacy règlement", limit=3)
    )
    top_sources = [h.entry.source_text for h in hits[:3]]
    assert {"GDPR", "privacy"} <= set(top_sources)
    # Exact acronym evidence must outrank vector-only noise.
    assert hits[0].entry.source_text in {"GDPR", "privacy"}


def test_recall_excludes_unknown_terms(tmp_path: Path) -> None:
    """Entries with zero evidence (vector + keyword) stay out of the hits."""
    store = _store(tmp_path)
    hits = store.hybrid_query(LexiconQuery(source_chunk="privacy", limit=5))
    sources = [h.entry.source_text for h in hits]
    assert "unrelated" not in sources
    assert sources[0] == "privacy"


def test_keyword_evidence_reported_on_hits(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hits = store.hybrid_query(LexiconQuery(source_chunk="GDPR privacy", limit=3))
    by_source = {h.entry.source_text: h for h in hits}
    assert by_source["GDPR"].keyword_score >= 0.8
    assert by_source["privacy"].keyword_score >= 0.8
