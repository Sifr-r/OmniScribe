from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
    TableNode,
)
from omniscribe.core.chunking.chunker import (
    ChunkingError,
    SectionAwareChunker,
    chunk_tree,
)
from omniscribe.core.chunking.taxonomy import (
    map_element_type,
)

# --- Unit Tests: Taxonomy Mapping ---

def test_taxonomy_mapping_block_types() -> None:
    assert map_element_type(BlockType.SECTION_HEADER) == "title"
    assert map_element_type(BlockType.PARAGRAPH) == "narrative"
    assert map_element_type(BlockType.TEXT) == "narrative"
    assert map_element_type(BlockType.LIST_ITEM) == "list_item"
    assert map_element_type(BlockType.TABLE) == "table"
    assert map_element_type(BlockType.FIGURE) == "figure"
    assert map_element_type(BlockType.EQUATION) == "formula"


def test_taxonomy_mapping_string_and_metadata() -> None:
    assert map_element_type("h1") == "title"
    assert map_element_type("bullet_point") == "list_item"
    assert map_element_type("chart") == "figure"
    assert map_element_type("latex") == "formula"
    assert map_element_type("grid") == "table"
    assert map_element_type("unknown_blob") == "narrative"

    # Metadata overrides
    assert map_element_type("text", {"is_title": True}) == "title"
    assert map_element_type("text", {"layout_role": "table"}) == "table"
    assert map_element_type("text", {"label": "equation"}) == "formula"


# --- Unit Tests: Knob Validation ---

def test_chunker_knob_validation() -> None:
    # max_chars must be positive
    with pytest.raises(ChunkingError, match="max_chars must be a positive integer"):
        SectionAwareChunker(max_chars=0)
    with pytest.raises(ChunkingError, match="max_chars must be a positive integer"):
        SectionAwareChunker(max_chars=-10)

    # overlap_chars must be non-negative and < max_chars
    with pytest.raises(ChunkingError, match="overlap_chars must be non-negative"):
        SectionAwareChunker(max_chars=1000, overlap_chars=-1)
    with pytest.raises(ChunkingError, match=r"overlap_chars .* strictly less than max_chars"):
        SectionAwareChunker(max_chars=1000, overlap_chars=1000)
    with pytest.raises(ChunkingError, match=r"overlap_chars .* strictly less than max_chars"):
        SectionAwareChunker(max_chars=1000, overlap_chars=1200)

    # min_chars must be positive and <= max_chars
    with pytest.raises(ChunkingError, match="min_chars must be a positive integer"):
        SectionAwareChunker(max_chars=1000, min_chars=0)
    with pytest.raises(ChunkingError, match=r"min_chars .* less than or equal to max_chars"):
        SectionAwareChunker(max_chars=500, min_chars=600)

    # Valid configurations succeed
    chunker = SectionAwareChunker(max_chars=1200, overlap_chars=120, min_chars=200)
    assert chunker.max_chars == 1200


# --- Unit Tests: Table Atomicity and Splitting ---

def test_table_stays_atomic_when_small() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)
    c1 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Col1", page_idx=0)
    c2 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Col2", page_idx=0)
    c3 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Val1", page_idx=0)
    c4 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Val2", page_idx=0)
    table = TableNode(
        rows=2,
        cols=2,
        page_idx=0,
        bbox=(0.1, 0.1, 0.9, 0.5),
        cells=[[c1, c2], [c3, c4]],
        block_id="table_1",
    )
    page.children.append(table)
    tree.pages.append(page)

    chunks = chunk_tree(tree, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].element_type == "table"
    assert chunks[0].block_ids == ["table_1"]
    assert "| Col1 | Col2 |" in chunks[0].text


def test_table_splits_when_oversized() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)

    # Header
    h1 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Item", page_idx=0)
    h2 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Description", page_idx=0)
    cells = [[h1, h2]]

    # 10 large rows
    for i in range(10):
        d1 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text=f"Item {i}", page_idx=0)
        d2 = BlockNode(block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="X" * 150, page_idx=0)
        cells.append([d1, d2])

    table = TableNode(
        rows=11,
        cols=2,
        page_idx=0,
        bbox=(0.1, 0.1, 0.9, 0.9),
        cells=cells,
        block_id="table_huge",
    )
    page.children.append(table)
    tree.pages.append(page)

    # max_chars=400 forces splitting across rows
    chunks = chunk_tree(tree, max_chars=400)
    assert len(chunks) > 1
    for c in chunks:
        assert c.element_type == "table"
        assert "| Item | Description |" in c.text  # Header repeated
        assert c.metadata.get("split") is True


# --- Hypothesis Property Tests: Invariants ---

@st.composite
def document_tree_strategy(draw: st.DrawFn) -> tuple[DocumentTree, dict[str, BlockNode]]:
    """Generate a realistic DocumentTree with varying block types, sections, lengths, and trust scores."""
    tree = DocumentTree()
    block_registry: dict[str, BlockNode] = {}
    num_pages = draw(st.integers(min_value=1, max_value=3))

    for page_idx in range(num_pages):
        page = PageTree(page_idx=page_idx)
        num_blocks = draw(st.integers(min_value=2, max_value=12))

        for b_idx in range(num_blocks):
            # Block type selection
            kind = draw(st.sampled_from([
                BlockType.SECTION_HEADER,
                BlockType.PARAGRAPH,
                BlockType.PARAGRAPH,
                BlockType.LIST_ITEM,
            ]))
            if kind == BlockType.SECTION_HEADER:
                text = f"Section {draw(st.text(min_size=3, max_size=20, alphabet=st.characters(whitelist_categories=('Lu', 'Ll', 'Nd'))))}"
                level = draw(st.integers(min_value=1, max_value=3))
            else:
                # Random length text, sometimes oversized
                char_count = draw(st.integers(min_value=20, max_value=600))
                text = "word " * (char_count // 5)
                level = 0

            trust = draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0)))
            b_id = f"b_p{page_idx}_b{b_idx}"
            node = BlockNode(
                block_type=kind,
                bbox=(0.1, 0.1, 0.9, 0.5),
                text=text,
                page_idx=page_idx,
                block_id=b_id,
                level=level,
                trust_score=trust,
            )
            page.children.append(node)
            block_registry[b_id] = node

        tree.pages.append(page)
    return tree, block_registry


@settings(max_examples=50)
@given(
    tree_and_registry=document_tree_strategy(),
    max_chars=st.integers(min_value=400, max_value=1500),
    overlap_chars=st.integers(min_value=0, max_value=100),
)
def test_chunker_invariants_hypothesis(
    tree_and_registry: tuple[DocumentTree, dict[str, BlockNode]],
    max_chars: int,
    overlap_chars: int,
) -> None:
    tree, block_registry = tree_and_registry
    min_chars = min(200, max_chars)

    chunks = chunk_tree(
        tree,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
        min_chars=min_chars,
    )

    # 1. Invariant: Max chars bound (unless a single block exceeds it)
    for c in chunks:
        if len(c.text) > max_chars:
            assert len(c.block_ids) == 1, (
                f"Chunk exceeds max_chars ({len(c.text)} > {max_chars}) but contains multiple blocks: {c.block_ids}"
            )
            single_block = block_registry.get(c.block_ids[0])
            if single_block:
                assert len(single_block.text.strip()) > max_chars

    # 2. Invariant: Boundaries at block edges
    for c in chunks:
        # Every block_id in chunk must be a known block
        for bid in c.block_ids:
            assert bid in block_registry

    # 3. Invariant: Overlap strictly preserved only within the same section
    for i in range(len(chunks) - 1):
        c1 = chunks[i]
        c2 = chunks[i + 1]
        shared_blocks = set(c1.block_ids).intersection(set(c2.block_ids))
        if shared_blocks:
            assert c1.section_path == c2.section_path, (
                f"Overlap detected across section boundaries! C1: {c1.section_path}, C2: {c2.section_path}"
            )

    # 4. Invariant: Minimum trust score correctly aggregated
    for c in chunks:
        constituent_scores: list[float] = []
        for bid in c.block_ids:
            if bid in block_registry:
                val = block_registry[bid].trust_score
                if val is not None:
                    constituent_scores.append(val)
        if constituent_scores:
            expected_min = min(constituent_scores)
            assert c.trust_score is not None
            assert abs(c.trust_score - expected_min) < 1e-6
        else:
            assert c.trust_score is None

    # 5. Invariant: Page span consistency
    for c in chunks:
        constituent_pages = [
            block_registry[bid].page_idx
            for bid in c.block_ids
            if bid in block_registry
        ]
        if constituent_pages:
            expected_span = (min(constituent_pages), max(constituent_pages))
            assert c.page_span == expected_span
