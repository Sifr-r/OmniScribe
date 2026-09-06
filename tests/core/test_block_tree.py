"""Tests for :mod:`omniscribe.core.block_tree` (DocumentTree IR)."""

from __future__ import annotations

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
    TableNode,
    from_document_result,
    from_pages_data,
)
from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult


def test_block_node_to_dict_round_trip():
    node = BlockNode(
        block_type=BlockType.SECTION_HEADER,
        bbox=(0.0, 0.1, 0.5, 0.15),
        text="Chapter 1",
        page_idx=0,
        level=1,
        confidence=0.92,
    )
    d = node.to_dict()
    assert d["block_type"] == "section_header"
    assert d["text"] == "Chapter 1"
    assert d["level"] == 1
    assert d["confidence"] == 0.92
    assert d["bbox"] == [0.0, 0.1, 0.5, 0.15]
    # block_id is non-empty
    assert isinstance(d["block_id"], str) and d["block_id"]


def test_block_node_trust_fields_round_trip():
    node = BlockNode(
        block_type=BlockType.PARAGRAPH,
        bbox=(0.0, 0.0, 0.5, 0.1),
        text="flagged text",
        page_idx=0,
        trust_score=0.42,
        trust_flags=("LOW_CALIBRATED_CONF",),
    )
    d = node.to_dict()
    assert d["trust_score"] == 0.42
    assert d["trust_flags"] == ["LOW_CALIBRATED_CONF"]
    restored = BlockNode.from_dict(d)
    assert restored.trust_score == 0.42
    assert restored.trust_flags == ("LOW_CALIBRATED_CONF",)


def test_block_node_trust_fields_default_none():
    node = BlockNode(
        block_type=BlockType.PARAGRAPH,
        bbox=(0.0, 0.0, 0.5, 0.1),
        text="clean",
        page_idx=0,
    )
    d = node.to_dict()
    assert d["trust_score"] is None
    assert d["trust_flags"] is None


def test_from_document_result_copies_trust_fields():
    page = DocumentPage(
        page_index=0,
        blocks=[
            DocumentBlock(
                bbox=(0.0, 0.0, 0.5, 0.1),
                text="flagged",
                trust_score=0.42,
                trust_flags=("LOW_CALIBRATED_CONF",),
            ),
            DocumentBlock(bbox=(0.0, 0.2, 0.5, 0.3), text="clean"),
        ],
    )
    tree = from_document_result(DocumentResult(pages=[page]))
    flagged = tree.pages[0].children[0]
    clean = tree.pages[0].children[1]
    assert flagged.trust_score == 0.42
    assert flagged.trust_flags == ("LOW_CALIBRATED_CONF",)
    assert clean.trust_score is None
    assert clean.trust_flags is None


def test_from_pages_data_basic():
    pages = {
        0: [
            ((0.0, 0.0, 1.0, 0.1), "INTRODUCTION"),
            ((0.0, 0.1, 1.0, 0.2), "This is a normal paragraph."),
        ],
        1: [
            ((0.0, 0.0, 1.0, 0.1), "CHAPTER 1"),
            ((0.0, 0.1, 1.0, 0.2), "Some body text."),
        ],
    }
    tree = from_pages_data(pages, source_path="doc.pdf")  # type: ignore[arg-type]
    assert tree.source_path == "doc.pdf"
    assert len(tree.pages) == 2
    assert tree.pages[0].page_idx == 0
    # All-caps short lines should be classified as SECTION_HEADER
    kinds = {n.block_type for n in tree.pages[0].children}
    assert BlockType.SECTION_HEADER in kinds
    assert BlockType.PARAGRAPH in kinds
    # round-trip through to_dict
    d = tree.to_dict()
    assert "pages" in d and len(d["pages"]) == 2


def test_document_tree_iter_text_blocks():
    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0, 1, 0.1),
                        text="hello",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PAGE_HEADER,
                        bbox=(0, 0, 1, 0.05),
                        text="HEADER",
                        page_idx=0,
                    ),
                ],
            )
        ]
    )
    blocks = tree.iter_text_blocks()
    # Both blocks are yielded by iter_text_blocks; translate_tree is the
    # function that skips headers/footers/numbers (see test below).
    texts = sorted(b.text for b in blocks)
    assert texts == ["HEADER", "hello"]


def test_table_node_to_dict_shape():
    table = TableNode(
        rows=2,
        cols=2,
        page_idx=0,
        bbox=(0, 0, 1, 0.5),
        cells=[
            [
                BlockNode(
                    block_type=BlockType.PARAGRAPH,
                    bbox=(0, 0, 0.5, 0.25),
                    text="A",
                    page_idx=0,
                ),
                BlockNode(
                    block_type=BlockType.PARAGRAPH,
                    bbox=(0.5, 0, 1, 0.25),
                    text="B",
                    page_idx=0,
                ),
            ]
        ],
    )
    d = table.to_dict()
    assert d["rows"] == 2 and d["cols"] == 2
    assert d["cells"][0][0]["text"] == "A"


def test_block_node_image_bytes_default_none():
    # Default-constructed BlockNode has no image_bytes; this is the
    # common case for non-figure blocks and the historical default.
    node = BlockNode(
        block_type=BlockType.PARAGRAPH,
        bbox=(0, 0, 1, 0.1),
        text="hello",
        page_idx=0,
    )
    assert node.image_bytes is None


def test_block_node_image_bytes_set_via_constructor():
    # BlockNode now declares `image_bytes` as a real field; the
    # `from_document_result` figure path passes the FigureNode's
    # bytes through here so html_writer can inline the image from
    # the page-children walk.
    payload = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
    node = BlockNode(
        block_type=BlockType.FIGURE,
        bbox=(0, 0, 1, 0.5),
        text="Figure 1",
        page_idx=0,
        image_bytes=payload,
    )
    assert node.image_bytes == payload


def test_block_node_image_bytes_to_dict_omits_payload():
    # `image_bytes` is intentionally NOT serialized in to_dict — the
    # canonical bytes are carried by FigureNode.image_bytes and
    # round-trip through `image_bytes_b64`. This guards against an
    # accidental reintroduction of the cross-reference in the JSON
    # artifact (which would break UTF-8 guarantees).
    node = BlockNode(
        block_type=BlockType.FIGURE,
        bbox=(0, 0, 1, 0.5),
        text="Figure 1",
        page_idx=0,
        image_bytes=b"\x89PNG\r\n",
    )
    d = node.to_dict()
    assert "image_bytes" not in d
    assert "image_bytes_b64" not in d
