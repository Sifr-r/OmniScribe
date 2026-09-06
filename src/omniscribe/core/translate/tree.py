"""Tree-aware translation.

Walks a :class:`DocumentTree`, translates each text block (preserving
structure), and writes the translation back into the tree. This is the
foundation for structure-preserving translation: headings stay headings,
tables stay tables, figures stay figures.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from omniscribe.core.block_tree import BlockNode
from omniscribe.core.translate.entity_memory import EntityMemory
from omniscribe.core.translate.glossary import Glossary
from omniscribe.core.translate.length_bands import effective_band
from omniscribe.core.translate.prompts import build_translation_prompt
from omniscribe.core.translate.config import TranslationSettings
from omniscribe.utils.prompt_safety import sanitize_prompt_input

if TYPE_CHECKING:
    from omniscribe.core.block_tree import DocumentTree
    from omniscribe.core.callbacks import TranslateChunkCallback

logger = logging.getLogger(__name__)


# A pluggable async callable that takes a prompt and returns translated text.
TranslatorFn = Callable[[str, str], Awaitable[str]]

# A pluggable async judge: (source_text, translated_text) -> (score, feedback).
EvaluatorFn = Callable[[str, str], Awaitable[tuple[float, str]]]


def build_context_block(
    glossary: Glossary,
    memory: EntityMemory,
    sliding_window: str = "",
) -> str:
    parts: list[str] = []
    gb = glossary.to_prompt_block()
    if gb:
        parts.append(gb)
    mb = memory.to_prompt_block()
    if mb:
        parts.append(mb)
    if sliding_window:
        parts.append(
            "PREVIOUS CONTEXT (do not translate again, just stay consistent):\n"
            + sliding_window
        )
    return "\n\n".join(parts)


def _truncate_words(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[-max_words:])


_SKIP_TYPES = {
    "page_header",
    "page_footer",
    "page_number",
    "figure",
}


async def translate_tree(
    tree: DocumentTree,
    *,
    target_language: str,
    translator: TranslatorFn,
    settings: TranslationSettings | None = None,
    glossary: Glossary | None = None,
    memory: EntityMemory | None = None,
    sliding_window_words: int = 80,
    dual_translate: bool = False,
    second_translator: TranslatorFn | None = None,
    evaluator: EvaluatorFn | None = None,
    on_translate_chunk: TranslateChunkCallback | None = None,
) -> DocumentTree:
    """Translate every text-bearing block in a :class:`DocumentTree`.

    ``translator(prompt, target_language) -> translated_text`` is the only
    LLM hook. The caller wires it up to the configured LLM (sync or async
    translation path), NLLBEngine, or any other back-end.

    When ``evaluator`` is supplied (an LLM-as-judge taking
    ``(source_text, translated_text) -> (score, feedback)``), each block
    goes through a bounded evaluate/retry loop keyed on
    ``settings.acceptance_score`` / ``settings.max_attempts``, and the
    best-scoring attempt wins.

    The function:

    - Walks every page's children in order
    - Skips ``PAGE_HEADER`` / ``PAGE_FOOTER`` / ``PAGE_NUMBER`` / ``FIGURE`` blocks
    - Builds a per-chunk prompt that injects glossary, entity memory, and
      the last ``sliding_window_words`` words of the previous translation
    - Writes the result back into ``block.text`` and ``block.metadata["translation"]``

    If ``on_translate_chunk`` is supplied, it is invoked once per
    successfully translated block with
    ``(chunk_idx, source_chars, translated_text, target_language)``.
    """

    glossary = glossary or Glossary()
    memory = memory or EntityMemory()
    active_settings = settings or TranslationSettings.from_env()
    last_window = ""
    chunk_idx = 0

    for page in tree.pages:
        for node in page.children:
            if isinstance(node, BlockNode):
                translated_text, last_window = await _translate_node(
                    node,
                    target_language=target_language,
                    translator=translator,
                    glossary=glossary,
                    memory=memory,
                    last_window=last_window,
                    sliding_window_words=sliding_window_words,
                    dual_translate=dual_translate,
                    second_translator=second_translator,
                    settings=active_settings,
                    evaluator=evaluator,
                )
                if translated_text is not None:
                    node.text = translated_text
                    node.metadata["translation"] = translated_text
                    if on_translate_chunk is not None:
                        # The chunk index is a per-call counter, not a
                        # global one — each translate_tree() invocation
                        # restarts from 0. Consumers that need a
                        # document-wide index can compute it from the
                        # block's tree position.
                        source_chars = len(node.text)
                        await on_translate_chunk(
                            chunk_idx,
                            source_chars,
                            translated_text,
                            target_language,
                        )
                        chunk_idx += 1
            elif hasattr(node, "cells"):
                for row in node.cells:
                    for cell in row:
                        if isinstance(cell, BlockNode):
                            translated_text, last_window = await _translate_node(
                                cell,
                                target_language=target_language,
                                translator=translator,
                                glossary=glossary,
                                memory=memory,
                                last_window=last_window,
                                sliding_window_words=sliding_window_words,
                                dual_translate=dual_translate,
                                second_translator=second_translator,
                                settings=active_settings,
                                evaluator=evaluator,
                            )
                            if translated_text is not None:
                                cell.text = translated_text
                                cell.metadata["translation"] = translated_text
                                if on_translate_chunk is not None:
                                    source_chars = len(cell.text)
                                    await on_translate_chunk(
                                        chunk_idx,
                                        source_chars,
                                        translated_text,
                                        target_language,
                                    )
                                    chunk_idx += 1
    return tree


async def _translate_node(
    node: BlockNode,
    *,
    target_language: str,
    translator: TranslatorFn,
    glossary: Glossary,
    memory: EntityMemory,
    last_window: str,
    sliding_window_words: int,
    dual_translate: bool,
    second_translator: TranslatorFn | None,
    settings: TranslationSettings,
    evaluator: EvaluatorFn | None,
) -> tuple[str | None, str]:
    if node.block_type.value in _SKIP_TYPES:
        return None, last_window
    if not node.text or not node.text.strip():
        return None, last_window

    # Update entity memory as we go.
    memory.add_text(node.text)

    prompt = build_translation_prompt(
        source_chunk=node.text,
        target_language=target_language,
        glossary_block=glossary.to_prompt_block() or None,
        entity_block=memory.to_prompt_block(max_items=settings.entity_memory_cap)
        or None,
        rag_context=None,
        sliding_window=last_window or None,
        feedback=None,
        block_type=node.block_type.value,
    )

    primary = _clean_translation(
        await translator(prompt, target_language), source=node.text
    )

    if dual_translate and second_translator is not None:
        secondary = _clean_translation(
            await second_translator(prompt, target_language), source=node.text
        )
        chosen = _pick_by_expected_length(node.text, primary, secondary)
    else:
        chosen = primary

    if evaluator is not None:
        chosen = await _judge_loop(
            source=node.text,
            first=chosen,
            prompt=prompt,
            target_language=target_language,
            translator=translator,
            evaluator=evaluator,
            settings=settings,
        )

    new_window = _truncate_words(chosen, sliding_window_words)
    return chosen, new_window


def _pick_by_expected_length(source: str, primary: str, secondary: str) -> str:
    """Pick the candidate whose length best matches the script-aware band.

    Cheap proxy for "didn't drop or hallucinate content", now aware that
    CJK→alphabetic translations legitimately expand (and vice versa) —
    the flat length-closeness pick mis-picked across scripts.
    """
    lo, hi = effective_band(source, primary)
    mid = (lo + hi) / 2.0
    src = max(1, len(source))

    def _deviation(candidate: str) -> float:
        return abs(len(candidate) / src - mid)

    return secondary if _deviation(secondary) < _deviation(primary) else primary


async def _judge_loop(
    *,
    source: str,
    first: str,
    prompt: str,
    target_language: str,
    translator: TranslatorFn,
    evaluator: EvaluatorFn,
    settings: TranslationSettings,
) -> str:
    """Bounded evaluate/retry loop; the best-scoring attempt wins."""
    best, best_score = first, -1.0
    current = first
    for attempt in range(1, settings.max_attempts + 1):
        score, feedback = await evaluator(source, current)
        if score > best_score:
            best, best_score = current, score
        if score >= settings.acceptance_score or attempt >= settings.max_attempts:
            break
        retry_prompt = (
            prompt
            + "\n\nPrevious translation had issues. Feedback: "
            + sanitize_prompt_input(feedback)
            + "\nPlease fix these issues.\n"
        )
        current = _clean_translation(
            await translator(retry_prompt, target_language), source=source
        )
        if current.startswith("[Translation Error"):
            break
    return best


# Common LLM preambles to strip from translation outputs.
_PREAMBLE_PATTERNS = [
    re.compile(
        r"^\s*(?:Here(?:'s| is) the translation|Translation|Sure[,!]?)[^\n]*\n+",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*```[a-zA-Z]*\n+"),
    re.compile(r"\n+\s*```\s*$"),
]


def _clean_translation(text: str, *, source: str) -> str:
    if not text:
        return text
    out = text.strip()
    for pat in _PREAMBLE_PATTERNS:
        out = pat.sub("", out)
    return out.strip()
