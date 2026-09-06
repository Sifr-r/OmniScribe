"""Tests for :mod:`omniscribe.core.translate.tree`."""

from __future__ import annotations

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
    TableNode,
)
from omniscribe.core.translate.entity_memory import EntityMemory
from omniscribe.core.translate.glossary import Glossary, GlossaryEntry
from omniscribe.core.translate.tree import (
    build_context_block,
    translate_tree,
)


def test_build_context_block_orders_sections():
    g = Glossary(entries=[GlossaryEntry(source="X", target="Y")])
    m = EntityMemory()
    m.add_text("Steve Jobs")
    block = build_context_block(g, m, "the quick brown fox")
    # All three sections appear
    assert "GLOSSARY" in block
    assert "PROPER NOUNS" in block
    assert "PREVIOUS CONTEXT" in block
    # Glossary comes first
    assert block.index("GLOSSARY") < block.index("PROPER NOUNS")
    assert block.index("PROPER NOUNS") < block.index("PREVIOUS CONTEXT")


async def test_translate_tree_skips_header_footer_number():
    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PAGE_HEADER,
                        bbox=(0, 0, 1, 0.05),
                        text="HEADER",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0.1, 1, 0.2),
                        text="body",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PAGE_FOOTER,
                        bbox=(0, 0.95, 1, 1),
                        text="FOOTER",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PAGE_NUMBER,
                        bbox=(0.4, 0.5, 0.6, 0.6),
                        text="42",
                        page_idx=0,
                    ),
                ],
            )
        ]
    )

    async def translator(prompt: str, lang: str) -> str:
        return "t"

    await translate_tree(
        tree,
        target_language="French",
        translator=translator,
    )
    # Headers/footers/page-numbers are unchanged
    assert tree.pages[0].children[0].text == "HEADER"  # type: ignore[union-attr]
    assert tree.pages[0].children[2].text == "FOOTER"  # type: ignore[union-attr]
    assert tree.pages[0].children[3].text == "42"  # type: ignore[union-attr]
    # The body paragraph was translated
    assert tree.pages[0].children[1].text == "t"  # type: ignore[union-attr]


async def test_translate_tree_writes_back_and_preserves_structure():
    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.SECTION_HEADER,
                        bbox=(0, 0, 1, 0.1),
                        text="Hello",
                        page_idx=0,
                        level=1,
                    ),
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0.1, 1, 0.2),
                        text="World",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PAGE_HEADER,
                        bbox=(0, 0.95, 1, 1),
                        text="pg 1",
                        page_idx=0,
                    ),
                ],
            )
        ]
    )

    async def translator(prompt: str, lang: str) -> str:
        return f"[{lang}] {prompt.split('SOURCE:')[-1].strip().splitlines()[0]}"

    await translate_tree(
        tree,
        target_language="French",
        translator=translator,
    )
    # The section header and paragraph were translated
    assert tree.pages[0].children[0].text.startswith("[French] Hello")  # type: ignore[union-attr]
    assert tree.pages[0].children[1].text.startswith("[French] World")  # type: ignore[union-attr]
    # The page header was skipped
    assert tree.pages[0].children[2].text == "pg 1"  # type: ignore[union-attr]
    # Translation metadata recorded
    assert "translation" in tree.pages[0].children[0].metadata  # type: ignore[union-attr]


async def test_translate_tree_sliding_window_propagates():
    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0, 1, 0.1),
                        text="alpha bravo charlie",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0.1, 1, 0.2),
                        text="delta echo foxtrot",
                        page_idx=0,
                    ),
                ],
            )
        ]
    )
    seen: list[str] = []

    async def translator(prompt: str, lang: str) -> str:
        seen.append(prompt)
        # Echo back a long string so the sliding window picks it up
        return ("ok " * 50).strip()

    await translate_tree(
        tree,
        target_language="Spanish",
        translator=translator,
        sliding_window_words=10,
    )
    # The second prompt should contain the PREVIOUS CONTEXT section
    assert "PREVIOUS CONTEXT" in seen[1]


async def test_translate_tree_dual_translate_chooses_secondary_when_closer():
    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0, 1, 0.1),
                        text="hi",  # very short source
                        page_idx=0,
                    )
                ],
            )
        ]
    )

    async def primary(prompt: str, lang: str) -> str:
        return "this is a much longer and hallucinated translation that drops nothing"

    async def secondary(prompt: str, lang: str) -> str:
        return "hi-traduit"  # very close in length to "hi"

    await translate_tree(
        tree,
        target_language="French",
        translator=primary,
        second_translator=secondary,
        dual_translate=True,
    )
    # The shorter, closer-length secondary should win
    assert tree.pages[0].children[0].text == "hi-traduit"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# TableNode cell translation (merged from test_phase2_table_node_translation.py,
# audit-secondary F26 / Phase 2)
# ---------------------------------------------------------------------------


async def test_translate_tree_translates_table_node_cells():
    """Verify TableNode cells are visited, translated, and emitted in on_translate_chunk.

    The original fix: ``translate_tree`` was previously bypassing
    TableNode instances in ``page.children``, leaving table cells
    untranslated. The fix recurses into ``TableNode.cells``.
    """
    cell_1 = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.0, 0.0, 0.5, 0.5),
        text="Hello",
        page_idx=0,
    )
    cell_2 = BlockNode(
        block_type=BlockType.TABLE,
        bbox=(0.5, 0.0, 1.0, 0.5),
        text="World",
        page_idx=0,
    )
    table = TableNode(
        rows=1,
        cols=2,
        page_idx=0,
        bbox=(0.0, 0.0, 1.0, 0.5),
        cells=[[cell_1, cell_2]],
    )
    page = PageTree(page_idx=0, children=[table])
    tree = DocumentTree(pages=[page])

    async def mock_translator(prompt: str, target_lang: str, **kwargs) -> str:
        # Extract source text from the prompt or return a translated string
        if "SOURCE:\nHello" in prompt:
            return "Hola"
        if "SOURCE:\nWorld" in prompt:
            return "Mundo"
        return f"Translated_{target_lang}"

    chunk_events: list[tuple[int, int, str, str]] = []

    async def on_chunk(chunk_idx: int, source_chars: int, translated: str, lang: str):
        chunk_events.append((chunk_idx, source_chars, translated, lang))

    await translate_tree(
        tree,
        target_language="Spanish",
        translator=mock_translator,
        on_translate_chunk=on_chunk,
    )

    assert cell_1.text == "Hola"
    assert cell_1.metadata["translation"] == "Hola"
    assert cell_2.text == "Mundo"
    assert cell_2.metadata["translation"] == "Mundo"
    assert len(chunk_events) == 2
    assert chunk_events[0] == (0, 4, "Hola", "Spanish")
    assert chunk_events[1] == (1, 5, "Mundo", "Spanish")


# ---------------------------------------------------------------------------
# Judge loop (LLM-remediation wave)
# ---------------------------------------------------------------------------


async def test_evaluator_retry_uses_feedback_and_keeps_best():
    calls: list[str] = []

    async def translator(prompt: str, lang: str) -> str:
        calls.append(prompt)
        if "Feedback:" in prompt:
            return "bonne traduction"
        return "mauvaise"

    async def evaluator(source: str, translated: str) -> tuple[float, str]:
        if translated == "bonne traduction":
            return (0.9, "ok")
        return (0.2, "wrong term")

    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0, 1, 0.1),
                        text="good source text",
                        page_idx=0,
                    )
                ],
            )
        ]
    )
    from omniscribe.core.translate.config import TranslationSettings

    out = await translate_tree(
        tree,
        target_language="French",
        translator=translator,
        evaluator=evaluator,
        settings=TranslationSettings(max_attempts=3),
    )
    block = out.pages[0].children[0]
    assert block.text == "bonne traduction"  # type: ignore[union-attr]
    assert any("Feedback:" in p for p in calls)


async def test_no_evaluator_is_single_call():
    n = 0

    async def translator(prompt: str, lang: str) -> str:
        nonlocal n
        n += 1
        return "ok"

    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0, 1, 0.1),
                        text="source",
                        page_idx=0,
                    )
                ],
            )
        ]
    )
    await translate_tree(tree, target_language="French", translator=translator)
    assert n == 1


async def test_judge_loop_never_returns_worse_retry():
    """A retry that scores lower than the first attempt must not win."""

    async def translator(prompt: str, lang: str) -> str:
        if "Feedback:" in prompt:
            return "worse retry"
        return "first attempt"

    async def evaluator(source: str, translated: str) -> tuple[float, str]:
        return (0.3, "meh") if translated == "first attempt" else (0.1, "worse")

    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0, 0, 1, 0.1),
                        text="source text",
                        page_idx=0,
                    )
                ],
            )
        ]
    )
    from omniscribe.core.translate.config import TranslationSettings

    out = await translate_tree(
        tree,
        target_language="French",
        translator=translator,
        evaluator=evaluator,
        settings=TranslationSettings(max_attempts=2),
    )
    assert out.pages[0].children[0].text == "first attempt"  # type: ignore[union-attr]
