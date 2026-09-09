from __future__ import annotations

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    EquationNode,
    PageTree,
    Span,
    TableNode,
)
from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult
from omniscribe.core.writers.exporter_base import BaseDocumentExporter
from omniscribe.core.writers.markdown import (
    MarkdownExporter,
    MarkdownWriter,
    render_markdown,
)


def test_markdown_writer_hierarchy() -> None:
    writer = MarkdownWriter()
    assert isinstance(writer, BaseDocumentExporter)
    assert MarkdownExporter is MarkdownWriter


def test_markdown_writer_headings() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)
    page.children = [
        BlockNode(
            block_type=BlockType.SECTION_HEADER,
            bbox=(0.1, 0.1, 0.9, 0.2),
            text="Top Title",
            page_idx=0,
            level=1,
        ),
        BlockNode(
            block_type=BlockType.SECTION_HEADER,
            bbox=(0.1, 0.2, 0.9, 0.3),
            text="Sub Heading",
            page_idx=0,
            level=2,
        ),
        BlockNode(
            block_type=BlockType.SECTION_HEADER,
            bbox=(0.1, 0.3, 0.9, 0.4),
            text="Deep Section",
            page_idx=0,
            level=4,
        ),
        BlockNode(
            block_type=BlockType.SECTION_HEADER,
            bbox=(0.1, 0.4, 0.9, 0.5),
            text="Hierarchy Header",
            page_idx=0,
            level=0,
            section_hierarchy=["A", "B", "C"],
        ),
    ]
    tree.pages.append(page)

    md = render_markdown(tree)
    assert "# Top Title" in md
    assert "## Sub Heading" in md
    assert "#### Deep Section" in md
    assert "### Hierarchy Header" in md


def test_markdown_writer_tables() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)

    cell_h1 = BlockNode(
        block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Name", page_idx=0
    )
    cell_h2 = BlockNode(
        block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Value | Units", page_idx=0
    )
    cell_d1 = BlockNode(
        block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="Speed\nMax", page_idx=0
    )
    cell_d2 = BlockNode(
        block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="120 km/h", page_idx=0
    )

    table = TableNode(
        rows=2,
        cols=2,
        page_idx=0,
        bbox=(0.1, 0.1, 0.9, 0.5),
        cells=[[cell_h1, cell_h2], [cell_d1, cell_d2]],
    )
    page.children.append(table)
    tree.pages.append(page)

    md = render_markdown(tree)
    assert "| Name | Value \\| Units |" in md
    assert "| --- | --- |" in md
    assert "| Speed Max | 120 km/h |" in md


def test_markdown_writer_standalone_table() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)
    tree.pages.append(page)

    cell_a = BlockNode(
        block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="A", page_idx=0
    )
    cell_b = BlockNode(
        block_type=BlockType.TEXT, bbox=(0, 0, 0, 0), text="B", page_idx=0
    )
    table = TableNode(
        rows=1,
        cols=2,
        page_idx=0,
        bbox=(0.1, 0.1, 0.9, 0.5),
        cells=[[cell_a, cell_b]],
    )
    tree.tables.append(table)

    md = render_markdown(tree)
    assert "| A | B |" in md
    assert "| --- | --- |" in md


def test_markdown_writer_figures() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)

    # 1. Figure with caption and artifact_ref in metadata
    fig1 = BlockNode(
        block_type=BlockType.FIGURE,
        bbox=(0.1, 0.1, 0.5, 0.5),
        text="",
        page_idx=0,
        metadata={"caption": "System Diagram", "artifact_ref": "art://img123"},
    )
    # 2. Figure with URL text
    fig2 = BlockNode(
        block_type=BlockType.FIGURE,
        bbox=(0.2, 0.2, 0.6, 0.6),
        text="https://example.com/logo.png",
        page_idx=0,
        metadata={"caption": "OmniScribe Logo"},
    )
    # 3. Figure fallback to bbox
    fig3 = BlockNode(
        block_type=BlockType.FIGURE,
        bbox=(0.1234, 0.2345, 0.5678, 0.6789),
        text="Plot of accuracy",
        page_idx=0,
    )

    page.children.extend([fig1, fig2, fig3])
    tree.pages.append(page)

    md = render_markdown(tree)
    assert "![System Diagram](art://img123)" in md
    assert "![OmniScribe Logo](https://example.com/logo.png)" in md
    assert "![Plot of accuracy](bbox:0.1234,0.2345,0.5678,0.6789)" in md


def test_markdown_writer_equations() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)
    eq1 = BlockNode(
        block_type=BlockType.EQUATION,
        bbox=(0.1, 0.1, 0.5, 0.2),
        text="E = mc^2",
        page_idx=0,
    )
    page.children.append(eq1)
    tree.pages.append(page)

    # Standalone EquationNode
    eq2 = EquationNode(
        page_idx=0,
        bbox=(0.1, 0.3, 0.5, 0.4),
        latex="\\int_0^\\infty e^{-x} dx = 1",
    )
    tree.equations.append(eq2)

    md = render_markdown(tree)
    assert "$$E = mc^2$$" in md
    assert r"$$\int_0^\infty e^{-x} dx = 1$$" in md


def test_markdown_writer_page_breaks() -> None:
    tree = DocumentTree()
    p0 = PageTree(page_idx=0)
    p0.children.append(
        BlockNode(
            block_type=BlockType.PARAGRAPH,
            bbox=(0, 0, 0, 0),
            text="Page 0 text",
            page_idx=0,
        )
    )
    p1 = PageTree(page_idx=1)
    p1.children.append(
        BlockNode(
            block_type=BlockType.PARAGRAPH,
            bbox=(0, 0, 0, 0),
            text="Page 1 text",
            page_idx=1,
        )
    )
    p2 = PageTree(page_idx=2)
    p2.children.append(
        BlockNode(
            block_type=BlockType.PARAGRAPH,
            bbox=(0, 0, 0, 0),
            text="Page 2 text",
            page_idx=2,
        )
    )
    tree.pages.extend([p0, p1, p2])

    md = render_markdown(tree)
    assert "<!-- PageBreak: 1 -->" in md
    assert "<!-- PageBreak: 2 -->" in md
    assert "<!-- PageBreak: 0 -->" not in md

    # Check suppression when include_page_breaks=False
    md_no_breaks = render_markdown(tree, include_page_breaks=False)
    assert "<!-- PageBreak" not in md_no_breaks


def test_markdown_writer_spans_lists_and_code() -> None:
    tree = DocumentTree()
    page = PageTree(page_idx=0)

    # Spans: bold, italic, code
    span_para = BlockNode(
        block_type=BlockType.PARAGRAPH,
        bbox=(0, 0, 0, 0),
        text="fallback",
        page_idx=0,
        spans=[
            Span(text="Bold text", bold=True),
            Span(text=" and "),
            Span(text="italic text", italic=True),
            Span(text=" and "),
            Span(text="code", code=True),
        ],
    )
    # List items
    li1 = BlockNode(
        block_type=BlockType.LIST_ITEM,
        bbox=(0, 0, 0, 0),
        text="Item 1",
        page_idx=0,
        level=0,
    )
    li2 = BlockNode(
        block_type=BlockType.LIST_ITEM,
        bbox=(0, 0, 0, 0),
        text="Nested item",
        page_idx=0,
        level=1,
    )
    # Code block
    code = BlockNode(
        block_type=BlockType.CODE,
        bbox=(0, 0, 0, 0),
        text="x = 42\ny = x + 1",
        page_idx=0,
    )

    page.children.extend([span_para, li1, li2, code])
    tree.pages.append(page)

    md = render_markdown(tree)
    assert "**Bold text** and *italic text* and `code`" in md
    assert "- Item 1" in md
    assert "  - Nested item" in md
    assert "```\nx = 42\ny = x + 1\n```" in md


def test_export_document_dispatch() -> None:
    doc = DocumentResult(
        pages=[
            DocumentPage(
                page_index=0,
                blocks=[
                    DocumentBlock(
                        bbox=(0.1, 0.1, 0.9, 0.2),
                        text="Document Title",
                        kind="section_header",
                    ),
                    DocumentBlock(
                        bbox=(0.1, 0.2, 0.9, 0.4),
                        text="Document narrative content.",
                        kind="paragraph",
                    ),
                ],
            )
        ]
    )
    writer = MarkdownWriter()
    out = writer.export_document(doc)
    assert "# Document Title" in out
    assert "Document narrative content." in out

    out2 = writer.export(doc)
    assert out2 == out
