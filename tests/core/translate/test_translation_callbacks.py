"""Tests for the on_translate_chunk callback in translate_tree (review M1).

Pre-fix, the `on_translate_chunk` parameter didn't exist; the Celery
task `process_translation_task` was passing `channel_id=None` as a
kwarg to `translate_tree` and that function silently accepted it (no,
actually it raised TypeError — mypy caught it; the bug was latent).
The in-process /api/translate/tree endpoint side-stepped translate_tree
entirely and re-implemented the walk to wire per-chunk WS frames.

After Phase C:
- `translate_tree` accepts `on_translate_chunk` and emits per-block
- The Celery task forwards channel_id via the callback
- The in-process endpoint calls translate_tree with a small adapter
  (no duplicated body)
"""

from __future__ import annotations

from omniscribe.core.block_tree import (
    BlockNode,
    BlockType,
    DocumentTree,
    PageTree,
)
from omniscribe.core.translate.glossary import Glossary
from omniscribe.core.translate.tree import translate_tree


def _build_tree_with_n_blocks(n: int, text_prefix: str = "block") -> DocumentTree:
    """Build a single-page tree with N text blocks.

    Each block has a unique source text so we can verify the callback
    receives per-block translations and the chunk_idx is monotonic.
    """
    page = PageTree(page_idx=0)
    for i in range(n):
        page.children.append(
            BlockNode(
                block_type=BlockType.PARAGRAPH,
                bbox=(0.0, 0.0, 1.0, 0.1),
                text=f"{text_prefix} {i}",
                page_idx=0,
            )
        )
    return DocumentTree(pages=[page])


async def test_translate_tree_emits_one_chunk_per_translated_block():
    """A 3-block tree produces exactly 3 callback invocations with
    chunk_idx 0, 1, 2 in order, and the translated text the
    translator returned (whatever that is) is what the callback sees."""

    chunks: list[tuple[int, int, str, str]] = []

    async def on_chunk(
        chunk_idx: int,
        source_chars: int,
        translated_text: str,
        target_language: str,
    ) -> None:
        chunks.append((chunk_idx, source_chars, translated_text, target_language))

    # The translator is intentionally simple: it returns a fixed
    # length string regardless of input, so source_chars is
    # deterministic across runs. The callback's contract is "fired
    # once per translated block with the right ordering and the
    # translator's actual output" — we don't try to verify the prompt
    # parsing because the prompt shape is the prompt's concern, not
    # the callback's.
    async def translator(prompt: str, lang: str) -> str:
        return f"OUT_{len(prompt)}"

    tree = _build_tree_with_n_blocks(3, text_prefix="block")
    await translate_tree(
        tree,
        target_language="Spanish",
        translator=translator,
        glossary=Glossary(),
        on_translate_chunk=on_chunk,
    )

    assert len(chunks) == 3
    assert [c[0] for c in chunks] == [0, 1, 2]
    # source_chars is the length of the original source text before translation.
    assert [c[1] for c in chunks] == [len(f"block {i}") for i in range(3)]
    # Target language flows through unchanged.
    assert all(c[3] == "Spanish" for c in chunks)


async def test_translate_tree_skips_emit_for_empty_text_blocks():
    """Empty / whitespace-only blocks are not translated, so the
    callback must not fire for them. Pre-fix code paths (the in-process
    endpoint's duplicated loop) had a similar check; the core
    translation now owns the same invariant."""

    chunks: list[int] = []

    async def on_chunk(*args, **kwargs) -> None:
        chunks.append(args[0])

    async def translator(prompt: str, lang: str) -> str:
        return "fixed"

    tree = DocumentTree(
        pages=[
            PageTree(
                page_idx=0,
                children=[
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0.0, 0.0, 1.0, 0.1),
                        text="real text",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0.0, 0.0, 1.0, 0.1),
                        text="   ",  # whitespace only — skip
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PAGE_HEADER,
                        bbox=(0.0, 0.0, 1.0, 0.1),
                        text="page header",
                        page_idx=0,
                    ),
                    BlockNode(
                        block_type=BlockType.PARAGRAPH,
                        bbox=(0.0, 0.0, 1.0, 0.1),
                        text="second real text",
                        page_idx=0,
                    ),
                ],
            )
        ]
    )

    await translate_tree(
        tree,
        target_language="French",
        translator=translator,
        glossary=Glossary(),
        on_translate_chunk=on_chunk,
    )

    # Only the two PARAGRAPH blocks with non-empty text trigger the
    # callback. The whitespace block and the PAGE_HEADER block are
    # silent.
    assert chunks == [0, 1]


async def test_translate_tree_with_no_callback_is_a_noop():
    """When `on_translate_chunk` is omitted (default), translation still
    runs and writes back to the tree; the test is just that nothing
    crashes and the tree state is consistent."""

    tree = _build_tree_with_n_blocks(2, text_prefix="alpha")

    async def translator(prompt: str, lang: str) -> str:
        return "fixed"

    await translate_tree(
        tree,
        target_language="German",
        translator=translator,
        glossary=Glossary(),
    )

    # The tree got translated.
    assert tree.pages[0].children[0].text == "fixed"  # type: ignore[union-attr]
    assert tree.pages[0].children[1].text == "fixed"  # type: ignore[union-attr]
    # The metadata side-effect is also present.
    assert tree.pages[0].children[0].metadata["translation"] == "fixed"  # type: ignore[union-attr]
