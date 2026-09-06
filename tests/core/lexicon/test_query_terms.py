"""Tests for lexicon query-term extraction."""

from __future__ import annotations

from omniscribe.core.lexicon.query_terms import candidate_terms


def test_acronyms_capitalized_and_quoted() -> None:
    terms = candidate_terms(
        'The EU adopted the "Digital Markets Act" promptly', limit=8
    )
    assert "EU" in terms
    assert "Digital Markets Act" in terms


def test_cjk_spans_extracted() -> None:
    terms = candidate_terms("See 東京都 and 서울특별시 today", limit=8)
    assert any("東京都" in t for t in terms)
    assert any("서울특별시" in t for t in terms)


def test_limit_and_dedupe() -> None:
    text = "EU EU EU NATO NATO WTO ASEAN UNICEF OPEC FIFA UEFA"
    terms = candidate_terms(text, limit=4)
    assert len(terms) <= 4
    assert len(terms) == len(set(terms))


def test_empty_text_yields_no_terms() -> None:
    assert candidate_terms("") == []
    assert candidate_terms("   ") == []


def test_casefold_dedupe_keeps_first_spelling() -> None:
    terms = candidate_terms("NASA nasa Nasa", limit=8)
    assert terms.count("NASA") == 1
