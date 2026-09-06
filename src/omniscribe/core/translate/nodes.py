"""LangGraph node builders + prompt helpers for the async translation loop.

Audit catalog (Sprint 6 long-file split): ``core/translate/workflow.py``
was 603 LOC mixing the LangGraph state schema, three node functions
(``retrieve_lexicon_context``, ``translate_node``, ``evaluate_node``),
their private helpers (``_llm_evaluate_translation``, ``_state_settings``,
``should_refine``), the prompt builder + JSON parser, the system
message constants, the graph assembly, the public ``run_translation``
entry point, and the ``_Chunker`` text chunker in one file.

This module is the node half: the node functions, the prompt
helpers they call, the system role messages, and the JSON parser.
``workflow.py`` keeps the LangGraph state schema, the graph
assembly, the public ``run_translation`` / ``chunk_text`` /
``get_translation_app`` entry points, and re-exports the
public node names so existing imports keep working.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from omniscribe.core.llm.client import call_llm
from omniscribe.core.llm.temperatures import (
    TEMPERATURE_EVALUATION,
    TEMPERATURE_TRANSLATION,
)
from omniscribe.core.translate.config import TranslationSettings
from omniscribe.core.translate.length_bands import effective_band
from omniscribe.core.translate.prompts import build_translation_prompt
from omniscribe.utils.prompt_safety import sanitize_prompt_input

logger = logging.getLogger(__name__)

# Bumped when the user-facing prompt body changes.
TRANSLATION_PROMPT_VERSION = "2026-08-15.v1"

# System role companion for the async translation loop. Same
# "preserve URLs / identifiers / brand names" guard the sync path
# uses, kept in one place.
TRANSLATION_SYSTEM_MESSAGE = (
    "You are a precise document translator. "
    "Preserve all markdown formatting, headings, lists, tables, and "
    "mathematical formulas exactly as they appear in the source. "
    "Do not translate URLs, code identifiers, file paths, or brand / "
    "product names — keep them unchanged. "
    "Do not add introductory or concluding comments, explanations, or "
    "meta-commentary. Output only the direct translation."
)

# System role companion for the LLM-as-judge evaluation step. The
# rubric itself stays in the user turn; this just sets the role and
# tells the judge what failure modes matter.
EVALUATION_SYSTEM_MESSAGE = (
    "You are a translation quality evaluator. "
    "Score the translation on a 0.0-1.0 scale using the supplied "
    "rubric. Flag terminology that doesn't match the supplied "
    "glossary, untranslated source-language fragments, and any "
    "URLs / identifiers / brand names that were altered. "
    "Respond with a single JSON object and nothing else."
)

# --- Quality thresholds ----------------------------------------------------
# Defaults for the translate/evaluate loop. The runtime values come from
# :class:`omniscribe.core.translate.config.TranslationSettings` (env-driven
# via ``OMNISCRIBE_TRANSLATION_*``); see refactor §2.8.
LEXICON_RESULT_COUNT = 3


# ---------------------------------------------------------------------------
# Deterministic adequacy checks
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s)>\"']+", re.IGNORECASE)
_ACRONYM_RE = re.compile(r"\b[A-Z0-9]{2,6}\b")
_NUMBER_RE = re.compile(r"\d[\d.,]*")


def deterministic_quality_issues(source: str, translated: str) -> list[str]:
    """Cheap, script-agnostic adequacy checks that run before the LLM judge.

    Catches failure modes a length-band check is blind to: altered URLs,
    dropped acronyms, and mangled numbers. Kept deterministic so no LLM
    call is spent on translations that are already provably lossy.
    """
    issues: list[str] = []
    src_urls = set(_URL_RE.findall(source))
    if src_urls and src_urls - set(_URL_RE.findall(translated)):
        issues.append(
            "URL(s) from the source are missing or altered in the translation."
        )
    src_acronyms = {m for m in _ACRONYM_RE.findall(source) if not m.isdigit()}
    translated_acronyms = set(_ACRONYM_RE.findall(translated))
    missing_acronyms = src_acronyms - translated_acronyms
    if missing_acronyms:
        issues.append(
            f"Acronym(s) {sorted(missing_acronyms)} not preserved in the translation."
        )
    src_numbers = set(_NUMBER_RE.findall(source))
    if src_numbers and src_numbers - set(_NUMBER_RE.findall(translated)):
        issues.append(
            "Numeric value(s) from the source are missing or altered in the translation."
        )
    return issues


# ---------------------------------------------------------------------------
# State Schema (re-exported from workflow.py for callers that grab it from
# either module — tests originally imported it from ``workflow`` and the
# LangGraph state lives in workflow.py to keep the graph assembly adjacent
# to the StateGraph type).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _optional_dependency_message(package: str) -> str:
    return (
        f"Async translation requires optional dependency '{package}'. "
        "Install the async translation extras to enable this feature."
    )


def _state_settings(state: Any) -> TranslationSettings:
    settings = state.get("settings")
    if settings is None:
        return TranslationSettings.from_env()
    if not isinstance(settings, TranslationSettings):
        raise ValueError("translation state settings must be TranslationSettings")
    return settings


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------


def retrieve_lexicon_context(state: Any) -> dict[str, list[str]]:
    """Retrieve glossary context for the current chunk via the LexiconStore.

    Replaces the legacy ChromaDB ``lanes_lexicon`` query (Phase 3 of the
    migration, see ``docs/lexicon-migration-spec.md`` §8). The store is
    process-lazy and fail-soft: if lancedb is missing, the store is empty,
    or any error fires, the function returns an empty ``rag_context`` and
    the graph proceeds without glossary injection.
    """
    try:
        from omniscribe.core.lexicon import LexiconQuery, get_default_store
    except ImportError:
        return {"rag_context": []}

    try:
        store = get_default_store()
    except Exception as exc:
        logger.warning("Lexicon store unavailable: %s", exc)
        return {"rag_context": []}

    settings = _state_settings(state)
    try:
        hits = store.hybrid_query(
            LexiconQuery(
                source_chunk=state["source_chunk"],
                source_lang=state.get("source_lang") or None,
                target_lang=state.get("target_lang") or state.get("target_language"),
                limit=settings.lexicon_result_count,
                min_score=settings.lexicon_min_score,
            )
        )
    except Exception as exc:
        logger.warning("Lexicon hybrid query failed: %s", exc)
        return {"rag_context": []}

    return {
        "rag_context": [h.entry.to_prompt_block_line() for h in hits],
    }


async def translate_node(state: Any) -> dict[str, str | int]:
    """Calls the LLM to translate the chunk, using RAG context.

    Optional state fields (Phase 4 additions):

    - ``glossary_prompt_block``: a DeepL-style ``style_rules`` block built
      from a :class:`omniscribe.core.translate.glossary.Glossary`.
    - ``entity_memory_prompt_block``: a context block listing proper nouns
      and dates from :class:`omniscribe.core.translate.entity_memory.EntityMemory`.
    - ``sliding_window``: a tail of the previous translation, used as
      auxiliary consistency context.

    Routes the LLM call through :func:`omniscribe.core.llm.client.call_llm`
    (same dispatcher as ``evaluate_node`` and ``api.services.ai._complete_text``)
    so retry / backoff / circuit-breaker behavior stays consistent across
    every code path — see refactor §2.2. Errors are surfaced as the
    ``[Translation Error: ...]`` prefix that ``evaluate_node`` already
    short-circuits on (``str.startswith('[Translation Error')``).
    """
    settings = _state_settings(state)

    prompt = build_translation_prompt(
        source_chunk=state["source_chunk"],
        target_language=state["target_language"],
        glossary_block=state.get("glossary_prompt_block"),
        entity_block=state.get("entity_memory_prompt_block"),
        rag_context=state.get("rag_context"),
        sliding_window=state.get("sliding_window"),
        feedback=state.get("feedback"),
        block_type=None,
    )

    try:
        translated = await call_llm(
            model=settings.model,
            api_base=settings.api_base,
            api_key=settings.api_key,
            temperature=TEMPERATURE_TRANSLATION,
            max_tokens=settings.max_tokens,
            system_prompt=TRANSLATION_SYSTEM_MESSAGE,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        translated = f"[Translation Error: {e}]"

    return {"translated_chunk": translated, "attempts": state.get("attempts", 0) + 1}


async def evaluate_node(state: Any) -> dict[str, float | str | bool]:
    """Evaluates translation quality; fail-safe, never fails open silently.

    Ordering of gates:

    1. Translation API failure (``[Translation Error`` prefix): revert to
       the best earlier attempt when one exists; mark ``failed`` when
       max attempts are exhausted (the caller raises — the error marker
       never reaches final output).
    2. ``evaluate_enabled=False`` → accept without judging (single-call path).
    3. Max attempts → return the best-scoring attempt, not the last.
    4. Trivial source (no letters / < 5 chars) → accept.
    5. Script-aware length band (``effective_band``) → reject out-of-band.
    6. Deterministic adequacy checks (URLs / acronyms / numbers) → reject
       provably lossy output without an LLM call.
    7. LLM judge. Judge errors or unparseable scores no longer count as a
       silent 1.0 pass — the attempt is accepted at ``acceptance_score``
       and flagged ``judge_unverified`` so the caller can observe it.
    """
    settings = _state_settings(state)
    attempts = state.get("attempts", 0)
    translated = state.get("translated_chunk", "")
    source = state.get("source_chunk", "")
    best_score = float(state.get("best_score", 0.0))
    best_translation = str(state.get("best_translation", ""))

    if translated.startswith("[Translation Error"):
        if best_translation and not best_translation.startswith("[Translation Error"):
            return {
                "evaluation_score": 1.0,
                "translated_chunk": best_translation,
                "feedback": "Reverted to best attempt.",
            }
        if attempts >= settings.max_attempts:
            return {
                "evaluation_score": 1.0,
                "failed": True,
                "translated_chunk": translated,
                "feedback": "Failed after max attempts.",
            }
        return {"evaluation_score": 0.0, "feedback": "Translation API call failed."}

    if not settings.evaluate_enabled:
        return {"evaluation_score": 1.0, "feedback": "Evaluation disabled."}

    if attempts >= settings.max_attempts:
        return {
            "evaluation_score": 1.0,
            "translated_chunk": best_translation or translated,
            "failed": False,
            "feedback": "",
        }

    has_letters = any(c.isalpha() for c in source)
    if not has_letters or len(source.strip()) < 5:
        return {"evaluation_score": 1.0, "feedback": "Looks good"}

    min_ratio, max_ratio = effective_band(source, translated)
    if len(translated) < len(source) * min_ratio:
        return {
            "evaluation_score": 0.0,
            "feedback": "Translation too short. Ensure you translate the entire chunk.",
        }

    if len(translated) > len(source) * max_ratio:
        return {
            "evaluation_score": 0.0,
            "feedback": "Translation too long. Likely garbled or padded output.",
        }

    issues = deterministic_quality_issues(source, translated)
    if issues:
        return {"evaluation_score": 0.0, "feedback": "; ".join(issues)}

    try:
        score, feedback = await _llm_evaluate_translation(state)
    except Exception as exc:
        logger.warning("LLM evaluation failed; accepting unverified: %s", exc)
        return {
            "evaluation_score": settings.acceptance_score,
            "judge_unverified": True,
            "feedback": "Judge unavailable.",
        }

    if score is None:
        logger.warning("Judge returned unparseable score; accepting unverified.")
        return {
            "evaluation_score": settings.acceptance_score,
            "judge_unverified": True,
            "feedback": feedback or "Judge output unparseable.",
        }

    updated: dict[str, float | str | bool] = {
        "evaluation_score": score,
        "feedback": feedback,
    }
    if score > best_score:
        updated["best_score"] = score
        updated["best_translation"] = translated
    return updated


async def _llm_evaluate_translation(state: Any) -> tuple[float | None, str]:
    """Run the configured LLM to score a translation and parse the JSON response.

    Returns ``(score, feedback)`` where ``score`` is ``None`` when the
    response carried no parseable score. Raises on LLM error so the caller
    can decide on a fallback; JSON-parse failures are converted to the
    ``None``-score pair inside ``parse_evaluation_response``.
    """
    settings = _state_settings(state)
    prompt = build_evaluation_prompt(
        source=state["source_chunk"],
        translation=state["translated_chunk"],
        target_language=state["target_language"],
        rag_context=list(state.get("rag_context") or []),
    )

    content = await call_llm(
        model=settings.model,
        api_base=settings.api_base,
        api_key=settings.api_key,
        temperature=TEMPERATURE_EVALUATION,
        max_tokens=settings.max_tokens,
        system_prompt=EVALUATION_SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": prompt}],
    )

    return parse_evaluation_response(content)


def build_evaluation_prompt(
    *,
    source: str,
    translation: str,
    target_language: str,
    rag_context: list[str],
) -> str:
    """Build the prompt asking the LLM to score a translation.

    The expected response shape is a JSON object::

        {"score": <float 0.0-1.0>, "feedback": "<str>", "issues": [<str>, ...]}

    Anything else falls back to ``(1.0, "")`` in :func:`parse_evaluation_response`.
    """
    # Both ``source`` and ``translation`` are user-controlled: ``source``
    # was the document text uploaded to /api/translate, and ``translation``
    # is whatever the upstream translation step produced (which itself
    # came from sanitized input but we don't assume the judge LLM is
    # the same model). Sanitize both at the prompt boundary.
    safe_source = sanitize_prompt_input(source)
    safe_translation = sanitize_prompt_input(translation)

    parts: list[str] = [
        "You are a translation quality evaluator. Score the translation below "
        f"into {target_language} on a 0.0-1.0 scale and explain any issues.\n",
        "SCORING RUBRIC:\n"
        "- 1.0: Faithful translation. No meaning loss. All glossary terms used correctly. "
        "URLs, identifiers, brand / product names preserved unchanged.\n"
        "- 0.7-0.9: Minor issues (one term missing, slight stylistic differences, "
        "one URL or identifier mishandled but still recognizable).\n"
        "- 0.4-0.6: Moderate issues (multiple terms missing, awkward phrasing, "
        "partial translation, untranslated source-language fragments left in, "
        "several URLs / identifiers altered).\n"
        "- 0.0-0.3: Major issues (untranslated, mistranslated, missing significant "
        "content, glossary terms substituted with wrong ones).\n",
    ]

    if rag_context:
        parts.append(
            "GLOSSARY (use these terms correctly; flag any deviation in `issues`):\n"
            + "\n".join(f"- {term}" for term in rag_context)
            + "\n"
        )

    parts.append(
        "FAILURE MODES TO FLAG IN `issues`:\n"
        "- Any glossary term in the SOURCE replaced with a different word in the translation.\n"
        "- Any URL, code identifier, file path, or brand / product name in the SOURCE\n"
        "  that was altered, transliterated, translated, or removed in the translation.\n"
        "- Any non-trivial source-language fragment (sentence, clause, or label) left\n"
        "  untranslated in the target-language output.\n"
        "- Any content present in the SOURCE that is missing from the translation.\n"
        "- Any hallucinated content in the translation that has no source equivalent.\n",
    )

    parts.append(
        "OUTPUT FORMAT:\n"
        "Respond with a single JSON object and nothing else:\n"
        '{"score": <float 0.0-1.0>, "feedback": "<one sentence>", '
        '"issues": ["<issue>", ...]}\n\n'
        f"SOURCE:\n{safe_source}\n\n"
        f"TRANSLATION ({target_language}):\n{safe_translation}"
    )

    return "".join(parts)


_FENCED_JSON_RE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\s*\Z", re.DOTALL | re.I)


def parse_evaluation_response(content: str) -> tuple[float | None, str]:
    """Parse the LLM's evaluation JSON into ``(score, feedback)``.

    Tolerant of fenced blocks and embedded objects. Behavior:

    - No parseable dict at all, or a missing / wrong-type score →
      ``(None, feedback)``. The caller decides the fail-safe policy
      (flag the evaluation unverified) — this parser no longer hands
      out silent passes.
    - Valid numeric score → clamp to ``[0, 1]`` and pair with feedback.
      ``bool`` is explicitly rejected as a score even though it subclasses
      ``int`` in Python, otherwise JSON ``true`` would silently pass as 1.0.
    """
    stripped = content.strip()
    if not stripped:
        return None, ""

    parsed = _extract_json_object(stripped)
    feedback = ""
    if parsed is not None:
        raw_feedback = parsed.get("feedback")
        if isinstance(raw_feedback, str):
            feedback = raw_feedback.strip()

    if parsed is None:
        return None, feedback

    raw_score = parsed.get("score")
    # bool is a subclass of int — guard against it coercing to 1.0 silently.
    if (
        raw_score is not None
        and not isinstance(raw_score, bool)
        and isinstance(raw_score, (int, float))
    ):
        return max(0.0, min(1.0, float(raw_score))), feedback

    # Score missing or wrong-type → no verdict; caller flags unverified.
    return None, feedback


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Find the first parseable JSON object in ``text``.

    Tries fenced code blocks first, then the raw text, then walks every ``{``
    index looking for a parseable object — same pattern as
    ``services.ai.parse_extraction_json`` but kept local to avoid cross-layer
    coupling between ``api.services`` and ``core``.
    """
    fenced = _FENCED_JSON_RE.match(text)
    candidates = [fenced.group(1).strip()] if fenced else []
    candidates.append(text)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    decoder = json.JSONDecoder()
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            parsed, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def should_refine(state: Any) -> str:
    """Router logic for conditional edge."""
    settings = _state_settings(state)
    if state.get("evaluation_score", 1.0) < settings.acceptance_score:
        return "translate"
    return "end"
