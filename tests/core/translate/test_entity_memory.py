"""Tests for :mod:`omniscribe.core.translate.entity_memory` (EntityMemory)."""

from __future__ import annotations

from omniscribe.core.translate.entity_memory import EntityMemory


def test_entity_memory_extracts_names_dates_acronyms():
    mem = EntityMemory()
    mem.add_text(
        "Steve Jobs founded Apple on January 9, 2007. The iPhone launched later that year."
    )
    mem.add_text("The NASA team joined IBM and MIT in 2010-03-15.")
    block = mem.to_prompt_block()
    # The dates and acronyms are picked up reliably.
    assert "January 9, 2007" in block
    assert "2010-03-15" in block
    assert "NASA" in block
    assert "IBM" in block
    assert "MIT" in block
    # Proper nouns: at least one of these is present (the regex picks up
    # the parts it can see). "Jobs" is always picked; "Apple" is picked
    # when preceded by whitespace.
    assert "Jobs" in block
    assert "Apple" in block


def test_entity_memory_picks_up_multiword_proper_nouns_with_lead_text():
    # A leading connector word lets the regex see "Steve Jobs" together.
    mem = EntityMemory()
    mem.add_text("We recall that Steve Jobs and Tim Cook worked at Apple Inc.")
    block = mem.to_prompt_block()
    assert "Steve Jobs" in block
    assert "Tim Cook" in block
    assert "Apple" in block


def test_entity_memory_is_empty():
    assert EntityMemory().is_empty()
    mem = EntityMemory()
    mem.add_text("plain text with no entities 1234")
    # 1234 alone isn't a date
    assert mem.is_empty() or len(mem.dates) == 0


def test_entity_memory_merge_combines():
    a = EntityMemory()
    a.add_text("We admire Steve Jobs and Wozniak")
    b = EntityMemory()
    b.add_text("People respect Musk and also Tesla Motors")
    merged = a.merge(b)
    # Both entities end up in the merged set
    assert "Steve Jobs" in merged.names
    assert "Musk" in merged.names
    assert "Wozniak" in merged.names
    # Multi-word names are also captured
    assert "Tesla Motors" in merged.names


def test_prompt_block_caps_by_frequency():
    mem = EntityMemory()
    for _ in range(5):
        mem.add_text("We saw Alice and Bob. Then Alice and Bob spoke.")
    block = mem.to_prompt_block(max_items=1)
    assert "Alice" in block
    assert "Bob" not in block


def test_cap_zero_items_drops_section():
    mem = EntityMemory()
    mem.add_text("We saw Alice and Bob.")
    assert mem.to_prompt_block(max_items=0) == ""


def test_no_cap_keeps_everything():
    mem = EntityMemory()
    mem.add_text("We saw Alice and Bob.")
    block = mem.to_prompt_block()
    assert "Alice" in block and "Bob" in block
