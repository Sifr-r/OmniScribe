"""Translate service: sync re-home, async tree runner, status mapping.

The sync path is a verbatim re-home of the pre-harness
``api/services/ai.py`` ``translate_text`` (commit ``44ef123^``), adapted
to harness settings resolution and the token-bound ``ArtifactStore``.
The module deliberately imports ``TRANSLATION_SYSTEM_MESSAGE`` from
``core.translate.nodes`` — the same system message the LangGraph workflow
uses — rather than redefining it. The async runner and its status
mapping are JobQueue-backed and live here alongside the sync path.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any, ClassVar, Protocol

from omniscribe.config import RuntimeSettings
from omniscribe.core.block_tree import BlockNode
from omniscribe.core.llm.client import call_llm
from omniscribe.core.llm.temperatures import (
    TEMPERATURE_EVALUATION,
    TEMPERATURE_TRANSLATION,
    TEMPERATURE_TRANSLATION_TREE,
)
from omniscribe.core.translate import TRANSLATION_SYSTEM_MESSAGE
from omniscribe.core.translate.config import (
    AsyncTranslationUnavailable,
    TranslationSettings,
)
from omniscribe.core.translate.entity_memory import EntityMemory
from omniscribe.core.translate.glossary import Glossary
from omniscribe.core.translate.nllb import NLLBEngine
from omniscribe.core.translate.nodes import (
    EVALUATION_SYSTEM_MESSAGE,
    build_evaluation_prompt,
    parse_evaluation_response,
)
from omniscribe.core.translate.tree import EvaluatorFn, TranslatorFn, translate_tree
from omniscribe.core.translate.workflow import get_translation_app
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.documents.service import build_tree, load_pages
from omniscribe.plugins.errors import PluginError
from omniscribe.plugins.jobs import JobOutcome, TranslationJobRunner
from omniscribe.plugins.translate.schemas import (
    AsyncTranslationRequest,
    TranslationRequest,
)
from omniscribe.utils.prompt_safety import sanitize_prompt_input
from omniscribe.utils.security import check_ssrf_target_sync

_LOGGER = logging.getLogger("omniscribe.plugins.translate")


class TranslateError(PluginError):
    """User-facing translate error (envelope wire fields on ``PluginError``)."""


def build_translation_prompt(text: str, target_language: str) -> str:
    """Verbatim re-home from ``api/services/ai.py`` (44ef123^)."""
    safe_text = sanitize_prompt_input(text)
    return (
        f"Translate the following document text into {target_language}. "
        f"Maintain all markdown formatting, headings, lists, tables, and mathematical formulas exactly. "
        f"Do not add any introductory or concluding comments, explanations, or meta-commentary. "
        f"Only output the direct translation.\n\n"
        f"TEXT:\n{safe_text}"
    )


def _parse_json_object(blob: bytes) -> dict[str, Any] | None:
    try:
        parsed = json.loads(blob)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_coordinates(
    request_base: str | None,
    request_key: str | None,
    request_model: str | None,
    settings: RuntimeSettings,
) -> tuple[str, str, str]:
    """Override → settings trio; SSRF-check the override only
    (pipeline_bridge trust boundary)."""
    if request_base and request_base.strip():
        check = check_ssrf_target_sync(request_base.strip())
        if not check.allowed:
            raise TranslateError(
                403,
                "ssrf_blocked",
                f"URL targets a blocked address: {check.reason}",
            )
    return (
        (request_base or settings.llm_api_base).strip(),
        (request_key or settings.llm_api_key).strip(),
        (request_model or settings.llm_model).strip(),
    )


async def translate_text(
    request: TranslationRequest,
    settings: RuntimeSettings,
    store: ArtifactStore | None = None,
) -> str:
    """Sync single-shot translation with a bounded evaluate/retry loop.

    ``store=None`` exists only so pure-function tests can call this
    without a store; the route always passes the injected store.

    The judge loop (LLM-remediation wave) is on by default
    (``OMNISCRIBE_TRANSLATION_EVALUATE``): after the first translation the
    same endpoint scores the output, and low-scoring translations are
    retried with the judge's feedback. The best-scoring attempt wins and
    judge outages never fail the request.
    """
    source_text = request.text.strip()
    if not source_text and request.text_artifact_id and request.text_artifact_token:
        if store is None:
            raise TranslateError(404, "not_found", "text artifact not found")
        blob = await store.get(request.text_artifact_id, request.text_artifact_token)
        if blob is None:
            raise TranslateError(404, "not_found", "text artifact not found")
        raw = _parse_json_object(blob.blob)
        if raw is not None:
            pages = load_pages(raw)
            source_text = "\n\n".join(
                "\n".join(lines) for _page, lines in sorted(pages.items())
            ).strip()

    if not source_text:
        return ""

    api_base, api_key, model = _resolve_coordinates(
        request.api_base, request.api_key, request.model, settings
    )
    # Env-driven loop knobs (evaluate toggle, budgets) with the request's
    # resolved endpoint coordinates on top.
    t_settings = replace(
        TranslationSettings.from_env(),
        api_base=api_base,
        api_key=api_key,
        model=model,
    )

    cache_key = _cache_key(source_text, request.target_language, api_base, model)
    cached = _translation_cache_get(cache_key)
    if cached is not None:
        return cached

    if not t_settings.evaluate_enabled:
        result = await _translate_once(
            source_text,
            request.target_language,
            "",
            api_base,
            api_key,
            model,
            t_settings.max_tokens,
        )
        _translation_cache_put(cache_key, result)
        return result

    best, best_score = "", -1.0
    retry_suffix = ""
    current = ""
    for attempt in range(1, t_settings.max_attempts + 1):
        try:
            current = await _translate_once(
                source_text,
                request.target_language,
                retry_suffix,
                api_base,
                api_key,
                model,
                t_settings.max_tokens,
            )
        except TranslateError:
            if best:
                return best
            raise
        score, feedback = await _judge_once(
            source_text,
            current,
            request.target_language,
            api_base,
            api_key,
            model,
            t_settings,
        )
        if score is not None and score > best_score:
            best, best_score = current, score
        if (
            score is None
            or score >= t_settings.acceptance_score
            or attempt >= t_settings.max_attempts
        ):
            break
        retry_suffix = (
            "\n\nPrevious translation had issues. Feedback: "
            + sanitize_prompt_input(feedback)
            + "\nPlease fix these issues.\n"
        )

    if not best:
        best = current
    _translation_cache_put(cache_key, best.strip())
    return best.strip()


async def _translate_once(
    source_text: str,
    target_language: str,
    extra_suffix: str,
    api_base: str,
    api_key: str,
    model: str,
    max_tokens: int,
) -> str:
    prompt = build_translation_prompt(source_text, target_language) + extra_suffix
    try:
        content = await call_llm(
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=TEMPERATURE_TRANSLATION,
            max_tokens=max_tokens,
            system_prompt=TRANSLATION_SYSTEM_MESSAGE,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        _LOGGER.exception("Translation request failed")
        raise TranslateError(502, "ai_error", "The AI service request failed.") from exc
    return content.strip()


async def _judge_once(
    source_text: str,
    translated: str,
    target_language: str,
    api_base: str,
    api_key: str,
    model: str,
    t_settings: TranslationSettings,
) -> tuple[float | None, str]:
    """Score a translation; returns ``(None, "")`` when the judge is unavailable."""
    prompt = build_evaluation_prompt(
        source=source_text,
        translation=translated,
        target_language=target_language,
        rag_context=[],
    )
    try:
        content = await call_llm(
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=TEMPERATURE_EVALUATION,
            max_tokens=t_settings.max_tokens,
            system_prompt=EVALUATION_SYSTEM_MESSAGE,
            prompt=prompt,
        )
    except Exception as exc:
        _LOGGER.warning("Translation judge unavailable; accepting unverified: %s", exc)
        return None, ""
    score, feedback = parse_evaluation_response(content)
    if score is None:
        _LOGGER.warning("Translation judge unparseable; accepting unverified.")
    return score, feedback


# --- Process-local LRU for the sync path ------------------------------------
# Keyed on (source hash, target, api_base, model). The sync path injects no
# RAG/glossary context, so no lexicon fingerprint is needed.
_TRANSLATION_CACHE_MAX = 256
_translation_cache: OrderedDict[tuple[str, str, str, str], str] = OrderedDict()


def _cache_key(
    source_text: str, target_language: str, api_base: str, model: str
) -> tuple[str, str, str, str]:
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return digest, target_language, api_base, model


def _translation_cache_get(key: tuple[str, str, str, str]) -> str | None:
    hit = _translation_cache.get(key)
    if hit is not None:
        _translation_cache.move_to_end(key)
    return hit


def _translation_cache_put(key: tuple[str, str, str, str], value: str) -> None:
    _translation_cache[key] = value
    _translation_cache.move_to_end(key)
    while len(_translation_cache) > _TRANSLATION_CACHE_MAX:
        _translation_cache.popitem(last=False)


@dataclass(frozen=True)
class _TranslatePayload:
    """One queued translation: submission id + the validated request."""

    # ClassVar dispatch marker (not a field): the jobs queue resolves the
    # runner registered under this service key at claim time.
    runner_protocol: ClassVar[type] = TranslationJobRunner

    submission_id: str
    request: AsyncTranslationRequest


class TranslationService(Protocol):
    async def submit(self, request: AsyncTranslationRequest) -> dict[str, str]: ...
    async def run_translate_job(self, payload: Any) -> JobOutcome: ...
    async def job_status(self, job_id: str) -> dict[str, Any] | None: ...
    def job_status_sync(self, record: Any) -> dict[str, Any]: ...
    async def translate_sync(self, request: TranslationRequest) -> str: ...
    async def translate_nllb(
        self, text: str, target_language: str
    ) -> dict[str, Any]: ...
    async def result(self, job_id: str, token: str) -> dict[str, Any] | None: ...


class TranslationServiceImpl:
    """Harness translation service over the JobQueue + ArtifactStore."""

    def __init__(
        self,
        settings: RuntimeSettings,
        queue: Any,
        store: Any,
        *,
        max_buffered_jobs: int = 500,
    ) -> None:
        self._settings = settings
        self._queue = queue
        self._store = store
        self._max_buffered_jobs = max_buffered_jobs
        self._submission_to_job: dict[str, str] = {}

    # -- submission ---------------------------------------------------------

    async def submit(self, request: AsyncTranslationRequest) -> dict[str, str]:
        # Availability first (cheap, cached), then artifact existence.
        try:
            get_translation_app()
        except AsyncTranslationUnavailable as exc:
            raise TranslateError(503, "backend_unavailable", str(exc)) from exc

        blob = await self._store.get(
            request.text_artifact_id, request.text_artifact_token
        )
        if blob is None:
            raise TranslateError(404, "not_found", "text artifact not found")

        submission_id = secrets.token_hex(16)
        handle = await self._queue.submit(
            _TranslatePayload(submission_id=submission_id, request=request),
            request_meta={
                "submission_id": submission_id,
                "target_language": request.target_language,
            },
        )
        self._submission_to_job[submission_id] = handle.job_id
        while len(self._submission_to_job) > self._max_buffered_jobs:
            self._submission_to_job.pop(next(iter(self._submission_to_job)), None)
        return {"job_id": handle.job_id, "status": "Processing"}

    # -- runner -------------------------------------------------------------

    async def run_translate_job(self, payload: Any) -> JobOutcome:
        if not isinstance(payload, _TranslatePayload):
            raise ValueError("translate job queue received a foreign payload")
        request = payload.request
        job_id = self._submission_to_job.get(payload.submission_id, "")

        blob = await self._store.get(
            request.text_artifact_id, request.text_artifact_token
        )
        if blob is None:
            raise FileNotFoundError("text artifact not found")
        raw = _parse_json_object(blob.blob)
        if raw is None:
            raise FileNotFoundError("text artifact not found")
        pages = load_pages(raw)
        tree = build_tree(pages)

        memory = EntityMemory()
        for lines in pages.values():
            for line in lines:
                memory.add_text(line)
        glossary = _build_glossary(request)
        t_settings = TranslationSettings.from_env()

        translator = _make_translator(
            request.api_base,
            request.api_key,
            request.model,
            self._settings,
            max_tokens=t_settings.max_tokens,
        )
        second_translator = None
        if request.dual_translate:
            second_translator = _make_translator(
                request.second_api_base,
                request.second_api_key,
                request.second_model,
                self._settings,
                max_tokens=t_settings.max_tokens,
            )
        evaluator: EvaluatorFn | None = None
        if t_settings.evaluate_enabled:
            evaluator = _make_evaluator(
                request.api_base,
                request.api_key,
                request.model,
                self._settings,
                t_settings,
                glossary,
                request.target_language,
            )

        translated_tree = await translate_tree(
            tree,
            target_language=request.target_language,
            translator=translator,
            settings=t_settings,
            glossary=glossary,
            memory=memory,
            sliding_window_words=request.sliding_window_words,
            dual_translate=request.dual_translate,
            second_translator=second_translator,
            evaluator=evaluator,
        )

        translated_pages = {
            str(page.page_idx): "\n".join(
                child.text
                for child in page.children
                if isinstance(child, BlockNode) and child.text
            )
            for page in translated_tree.pages
        }
        blocks_translated = sum(
            1
            for page in translated_tree.pages
            for child in page.children
            if isinstance(child, BlockNode) and child.text
        )
        translated_handle = await self._store.put(
            json.dumps(translated_pages).encode("utf-8"),
            content_type="application/json",
            owner_job_id=job_id,
        )
        summary = {
            "artifact_id": request.text_artifact_id,
            # Deliberately NO translated_artifact_token: the status endpoint
            # is unauthenticated (audit C-3/H-3 semantics).
            "translated_artifact_id": translated_handle.id,
            "page_count": len(translated_tree.pages),
            "blocks_translated": blocks_translated,
        }
        return JobOutcome(
            blob=json.dumps(summary).encode("utf-8"),
            content_type="application/json",
        )

    # -- status -------------------------------------------------------------

    def job_status_sync(self, record: Any) -> dict[str, Any]:
        """Map one JobRecord to the client's Celery-era status vocabulary."""
        body: dict[str, Any] = {"job_id": record.job_id}
        if record.status == "queued":
            body.update(state="PENDING", status="Pending...")
        elif record.status == "running":
            body.update(state="PROGRESS", status="Processing...")
        elif record.status == "error":
            body.update(
                state="FAILURE",
                status="Failed",
                error="internal_error",
                detail="The translation job failed.",
            )
        elif record.status == "cancelled":
            body.update(
                state="FAILURE",
                status="Cancelled",
                error="cancelled",
                detail="Translation was cancelled.",
            )
        else:  # complete
            body.update(state="SUCCESS", status="Completed")
        return body

    async def job_status(self, job_id: str) -> dict[str, Any] | None:
        record = await self._queue.status(job_id)
        if record is None:
            return None
        body = self.job_status_sync(record)
        if record.status == "complete":
            result = await self._load_result(record)
            if result is not None:
                body["result"] = result
        return body

    async def _load_result(self, record: Any) -> dict[str, Any] | None:
        if not record.result_artifact_id or not record.result_artifact_token:
            return None
        blob = await self._store.get(
            record.result_artifact_id, record.result_artifact_token
        )
        if blob is None:
            return None
        return _parse_json_object(blob.blob)

    async def result(self, job_id: str, token: str) -> dict[str, Any] | None:
        """Token-redeeming async result fetch (ride-along; audit C-3/H-3)."""
        record = await self._queue.status(job_id)
        if (
            record is None
            or record.status != "complete"
            or not record.result_artifact_id
            or not record.result_artifact_token
            or not secrets.compare_digest(token, record.result_artifact_token)
        ):
            return None
        blob = await self._store.get(record.result_artifact_id, token)
        if blob is None:
            return None
        return _parse_json_object(blob.blob)

    # -- sync + nllb --------------------------------------------------------

    async def translate_sync(self, request: TranslationRequest) -> str:
        return await translate_text(request, self._settings, store=self._store)

    async def translate_nllb(self, text: str, target_language: str) -> dict[str, Any]:
        if not text.strip():
            raise TranslateError(422, "bad_request", "'text' is required")
        engine = _get_nllb_engine()
        if not engine.is_available():
            raise TranslateError(
                503,
                "backend_unavailable",
                "NLLBEngine is not available. Install the 'nllb' extra: uv sync --extra nllb",
            )
        try:
            result = await engine.translate(text, target_language)
        except Exception as exc:
            _LOGGER.exception("NLLB translation request failed")
            raise TranslateError(
                502, "ai_error", "The AI service request failed."
            ) from exc
        return {
            "translated_text": result.text,
            "source_lang": result.source_lang,
            "target_lang": result.target_lang,
        }


def _build_glossary(request: AsyncTranslationRequest) -> Glossary | None:
    # Old-route precedence (verified): entries win over paired-lines text.
    if request.glossary:
        return Glossary.from_dict({"entries": request.glossary})
    if request.glossary_text:
        return Glossary.from_paired_lines(request.glossary_text)
    return None


def _make_translator(
    request_base: str | None,
    request_key: str | None,
    request_model: str | None,
    settings: RuntimeSettings,
    *,
    max_tokens: int | None = None,
) -> TranslatorFn:
    api_base, api_key, model = _resolve_coordinates(
        request_base, request_key, request_model, settings
    )

    async def translator(prompt: str, target_language: str) -> str:
        return await call_llm(
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=TEMPERATURE_TRANSLATION_TREE,
            max_tokens=max_tokens,
            system_prompt=TRANSLATION_SYSTEM_MESSAGE,
            prompt=prompt,
        )

    return translator


def _make_evaluator(
    request_base: str | None,
    request_key: str | None,
    request_model: str | None,
    settings: RuntimeSettings,
    t_settings: TranslationSettings,
    glossary: Glossary | None,
    target_language: str,
) -> EvaluatorFn:
    """Build the per-job LLM-as-judge for the async tree path.

    The judge reuses the primary translator's endpoint coordinates and
    receives the request-supplied glossary lines as its reference terms.
    """
    api_base, api_key, model = _resolve_coordinates(
        request_base, request_key, request_model, settings
    )
    glossary_lines = [
        line
        for line in (glossary.to_prompt_block().splitlines() if glossary else [])
        if line.startswith("- ")
    ]

    async def evaluator(source: str, translated: str) -> tuple[float | None, str]:
        prompt = build_evaluation_prompt(
            source=source,
            translation=translated,
            target_language=target_language,
            rag_context=glossary_lines,
        )
        content = await call_llm(
            model=model,
            api_base=api_base,
            api_key=api_key,
            temperature=TEMPERATURE_EVALUATION,
            max_tokens=t_settings.max_tokens,
            system_prompt=EVALUATION_SYSTEM_MESSAGE,
            prompt=prompt,
        )
        return parse_evaluation_response(content)

    return evaluator


# ---------------------------------------------------------------------------
# NLLB fast path
# ---------------------------------------------------------------------------

_NLLB_ENGINE: NLLBEngine | None = None


def _get_nllb_engine() -> NLLBEngine:
    # Module-level singleton: the engine lazily loads the transformers
    # pipeline on first use and caches it per instance. The old server
    # constructed a fresh engine per request, reloading the model every
    # call; the singleton keeps the contract while dropping the reload.
    global _NLLB_ENGINE
    if _NLLB_ENGINE is None:
        _NLLB_ENGINE = NLLBEngine()
    return _NLLB_ENGINE
