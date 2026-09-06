"""Single prompt builder shared by the LangGraph path and the tree path.

One builder means both paths share sanitization, section ordering, and
type hints; drift between the two prompt systems was an audit finding.
Every externally-sourced section is sanitized here so injection sites
can't forget.
"""

from __future__ import annotations

from omniscribe.utils.prompt_safety import sanitize_prompt_input

_TYPE_HINTS = {
    "section_header": (
        "\nNOTE: This is a document heading. Translate it as a concise heading; "
        "do not add punctuation.\n"
    ),
    "list_item": "\nNOTE: This is a list item. Keep it terse; preserve list semantics.\n",
    "key_value": (
        "\nNOTE: This is a key-value pair. Translate only the value; keep keys "
        "intact if they're labels (e.g. 'Invoice Number').\n"
    ),
}


def build_translation_prompt(
    *,
    source_chunk: str,
    target_language: str,
    glossary_block: str | None,
    entity_block: str | None,
    rag_context: list[str] | None,
    sliding_window: str | None,
    feedback: str | None,
    block_type: str | None = None,
) -> str:
    """Build one translation user-turn prompt from all context sections.

    Every externally-sourced section (glossary block, entity block, RAG
    lines, sliding window, feedback, source chunk) is sanitized at this
    single boundary — glossary entries and lexicon lines come from
    user-uploaded imports, so a crafted entry must not be able to inject
    instructions into the prompt.
    """
    if block_type == "code":
        return (
            "Translate only the natural-language parts of the following code block. "
            "Do not translate code identifiers, function names, or string literals. "
            f"Target language: {target_language}.\n\n"
            f"```\n{sanitize_prompt_input(source_chunk)}\n```\n"
        )

    parts: list[str] = [
        f"Translate the following text into {target_language}. "
        "Preserve formatting, line breaks, and any inline runs.\n"
    ]
    if block_type in _TYPE_HINTS:
        parts.append(_TYPE_HINTS[block_type] + "\n")
    if glossary_block:
        parts.append(sanitize_prompt_input(glossary_block) + "\n\n")
    if entity_block:
        parts.append(sanitize_prompt_input(entity_block) + "\n\n")
    if rag_context:
        parts.append(
            "Use the following lexicon definitions to ensure correct terminology:\n"
            + sanitize_prompt_input("\n".join(rag_context))
            + "\n\n"
        )
    if sliding_window:
        parts.append(
            "PREVIOUS CONTEXT (do not translate again, just stay consistent):\n"
            + sanitize_prompt_input(sliding_window)
            + "\n\n"
        )
    if feedback:
        parts.append(
            "Previous translation had issues. Feedback: "
            f"{sanitize_prompt_input(feedback)}\nPlease fix these issues.\n\n"
        )
    parts.append(f"SOURCE:\n{sanitize_prompt_input(source_chunk)}")
    return "".join(parts)
