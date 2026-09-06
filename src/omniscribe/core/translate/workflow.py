"""Async translation LangGraph workflow — assembly, state, public entry.

The node functions (``retrieve_lexicon_context``, ``translate_node``,
``evaluate_node``, ``should_refine``) and their prompt / parser
helpers live in ``omniscribe.core.translate.nodes``. This module
owns the graph assembly, the LangGraph state schema, the
``_Chunker`` / ``chunk_text`` text splitter, the lazy
``get_translation_app`` / ``translation_app`` exports, and the
public ``run_translation`` convenience entry point.

Public surface re-exported from ``nodes`` for backward
compatibility (tests and the API layer import these names from
``omniscribe.core.translate.workflow``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, TypedDict

from omniscribe.core.translate.config import (
    AsyncTranslationUnavailable,
    TranslationError,
    TranslationSettings,
)
from omniscribe.core.translate.nodes import (
    _FENCED_JSON_RE,  # noqa: F401  (re-export for tests)
    _extract_json_object,  # noqa: F401  (re-export for tests)
    _llm_evaluate_translation,  # noqa: F401  (re-export for tests)
    _optional_dependency_message,
    _state_settings,  # noqa: F401  (re-export for tests)
    build_evaluation_prompt,
    evaluate_node,
    parse_evaluation_response,
    retrieve_lexicon_context,
    should_refine,
    translate_node,
)


# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------
class TranslationState(TypedDict, total=False):
    source_chunk: str
    target_language: str
    # Optional language codes for hybrid lexicon filtering (spec §8.1).
    # When present, the lexicon query filters by source_lang/target_lang
    # so a glossary scoped to en→fr doesn't bleed into a de→es request.
    # Populated by the translation route when known (request field, OCR
    # document metadata, or inference).
    source_lang: str
    target_lang: str
    rag_context: list[str]
    translated_chunk: str
    evaluation_score: float
    feedback: str
    attempts: int
    settings: TranslationSettings
    # Fail-safe judge bookkeeping (LLM-remediation wave): the graph keeps
    # the best-scoring attempt so a late bad retry can't win, and marks
    # ``failed`` when every attempt errored (caller raises TranslationError).
    best_translation: str
    best_score: float
    judge_unverified: bool
    failed: bool
    # Phase 4 additions (test-pinned passthroughs — populated by
    # tests/core/translate/test_translation_boundary.py)
    glossary_prompt_block: str
    entity_memory_prompt_block: str
    sliding_window: str


# ---------------------------------------------------------------------------
# Build the Graph
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_translation_app() -> Any:
    """Return the compiled LangGraph app, building it only when invoked."""

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise AsyncTranslationUnavailable(
            _optional_dependency_message("langgraph")
        ) from exc

    workflow = StateGraph(TranslationState)
    workflow.add_node("retrieve", retrieve_lexicon_context)
    workflow.add_node("translate", translate_node)
    workflow.add_node("evaluate", evaluate_node)

    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "translate")
    workflow.add_edge("translate", "evaluate")
    workflow.add_conditional_edges(
        "evaluate", should_refine, {"translate": "translate", "end": END}
    )

    return workflow.compile()


class _LazyTranslationApp:
    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return get_translation_app().invoke(*args, **kwargs)


translation_app = _LazyTranslationApp()


class _Chunker:
    """Encapsulates paragraph → line → word hierarchical chunking state."""

    def __init__(self, max_chunk_size: int):
        self.max_chunk_size = max_chunk_size
        self.chunks: list[str] = []
        self._current: str = ""

    def add(self, text: str, delim: str = "") -> None:
        """Add text to current chunk; flush if exceeds max_chunk_size."""
        if not text:
            return

        if not self._current:
            self._current = text
            return

        candidate = self._current + delim + text
        if len(candidate) > self.max_chunk_size:
            self.chunks.append(self._current)
            self._current = text
        else:
            self._current = candidate

    def _flush(self) -> None:
        """Flush current chunk to output."""
        if self._current:
            self.chunks.append(self._current)
            self._current = ""

    def finalize(self) -> list[str]:
        """Return all chunks and clear state."""
        self._flush()
        return [c for c in self.chunks if c.strip()]


def chunk_text(text: str, max_chunk_size: int = 4000) -> list[str]:
    """Splits text into chunks of maximum size, trying to preserve paragraph and sentence boundaries."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if max_chunk_size < 1:
        raise ValueError("max_chunk_size must be greater than zero")
    if not text:
        return []
    if len(text) <= max_chunk_size:
        return [text]

    chunker = _Chunker(max_chunk_size)

    # Split by paragraphs first
    for paragraph in text.split("\n\n"):
        if len(paragraph) <= max_chunk_size - 4:  # -4 for delimiter overhead
            chunker.add(paragraph, "\n\n")
        else:
            # Paragraph is too large, split by lines
            for line in paragraph.split("\n"):
                if len(line) <= max_chunk_size - 2:  # -2 for line delimiter
                    chunker.add(line, "\n")
                else:
                    # Line is too large, split by words
                    for word in line.split(" "):
                        chunker.add(word, " ")

    return chunker.finalize()


def run_translation(
    text: str,
    target_language: str = "English",
    settings: TranslationSettings | None = None,
) -> str:
    """Convenience function to run the compiled graph on a text by chunking it to prevent LLM context overflow."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(target_language, str) or not target_language.strip():
        raise ValueError("target_language must be a non-empty string")
    if not text.strip():
        return ""

    active_settings = settings or TranslationSettings.from_env()
    chunks = chunk_text(text)
    translated_chunks: list[str] = []
    app = get_translation_app()

    for chunk in chunks:
        initial_state: TranslationState = {
            "source_chunk": chunk,
            "target_language": target_language,
            "rag_context": [],
            "translated_chunk": "",
            "evaluation_score": 1.0,
            "feedback": "",
            "attempts": 0,
            "settings": active_settings,
        }
        result = app.invoke(initial_state)
        if result.get("failed"):
            raise TranslationError(
                f"Translation failed after {active_settings.max_attempts} attempts "
                f"for chunk starting: {chunk[:80]!r}"
            )
        translated = result.get("translated_chunk", "")
        if translated:
            translated_chunks.append(translated)

    return "\n\n".join(translated_chunks)


__all__ = [
    "TranslationState",
    "_Chunker",
    "build_evaluation_prompt",
    "chunk_text",
    "evaluate_node",
    "get_translation_app",
    "parse_evaluation_response",
    "retrieve_lexicon_context",
    "run_translation",
    "should_refine",
    "translate_node",
    "translation_app",
]
