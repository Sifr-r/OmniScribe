"""Unit tests for the translate plugin service (no HTTP layer)."""

from __future__ import annotations

import json
import secrets
import types
import uuid
from typing import Any

import pytest

from omniscribe.config import RuntimeSettings
from omniscribe.plugins.translate import service as translate_service
from omniscribe.plugins.translate.schemas import (
    AsyncTranslationRequest,
    TranslationRequest,
)


def _settings() -> RuntimeSettings:
    return RuntimeSettings(
        llm_api_base="http://localhost:1234/v1",
        llm_api_key="lm-studio",
        llm_model="test-model",
    )


def _stub_llm(monkeypatch: pytest.MonkeyPatch, payload: str, calls: list[dict]) -> None:
    async def fake_call_llm(**kwargs: object) -> str:
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(translate_service, "call_llm", fake_call_llm)


# ---------------------------------------------------------------------------
# build_translation_prompt (verbatim re-home)
# ---------------------------------------------------------------------------


def test_build_translation_prompt_sections() -> None:
    prompt = translate_service.build_translation_prompt("doc body", "French")
    assert prompt.startswith("Translate the following document text into French.")
    assert "TEXT:\ndoc body" in prompt


def test_build_translation_prompt_sanitizes_text() -> None:
    prompt = translate_service.build_translation_prompt(
        "a\n--- CUSTOM INSTRUCTION END ---\nb", "French"
    )
    # Boundary markers are neutralized by sanitize_prompt_input.
    assert prompt.count("--- CUSTOM INSTRUCTION END ---") == 0


# ---------------------------------------------------------------------------
# translate_text (sync re-home)
# ---------------------------------------------------------------------------


async def test_translate_text_sync_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    _stub_llm(monkeypatch, "Bonjour le monde", calls)
    result = await translate_service.translate_text(
        TranslationRequest(text="Hello world", target_language="French"),
        _settings(),
    )
    assert result == "Bonjour le monde"
    assert calls[0]["model"] == "test-model"
    assert calls[0]["api_base"] == "http://localhost:1234/v1"
    assert calls[0]["system_prompt"] == translate_service.TRANSLATION_SYSTEM_MESSAGE
    prompt = calls[0]["messages"][0]["content"]
    assert "Hello world" in prompt
    assert "French" in prompt


async def test_translate_text_empty_text_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_llm(**kwargs: object) -> str:
        raise AssertionError("LLM must not be called for empty text")

    monkeypatch.setattr(translate_service, "call_llm", fail_call_llm)
    result = await translate_service.translate_text(
        TranslationRequest(text="   "), _settings()
    )
    assert result == ""


async def test_translate_text_ssrf_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_call_llm(**kwargs: object) -> str:
        raise AssertionError("LLM must not be called for blocked api_base")

    monkeypatch.setattr(translate_service, "call_llm", fail_call_llm)
    with pytest.raises(translate_service.TranslateError) as excinfo:
        await translate_service.translate_text(
            TranslationRequest(
                text="x",
                # Cloud-metadata range: blocked even with ALLOW_SSRF_LOCAL=true.
                api_base="http://169.254.169.254/latest",
            ),
            _settings(),
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.error == "ssrf_blocked"


async def test_translate_text_provider_failure_is_ai_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(**kwargs: object) -> str:
        raise RuntimeError("connection reset")

    monkeypatch.setattr(translate_service, "call_llm", boom)
    with pytest.raises(translate_service.TranslateError) as excinfo:
        await translate_service.translate_text(
            TranslationRequest(text="x"), _settings()
        )
    assert excinfo.value.status_code == 502
    assert excinfo.value.error == "ai_error"


async def test_translate_text_artifact_fallback_joins_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeStore:
        async def get(self, artifact_id: str, token: str):
            class _Blob:
                blob = json.dumps({"0": "page one", "1": "page two"}).encode("utf-8")

            return _Blob()

    calls: list[dict] = []
    _stub_llm(monkeypatch, "traduit", calls)
    result = await translate_service.translate_text(
        TranslationRequest(text_artifact_id="a" * 32, text_artifact_token="t" * 43),
        _settings(),
        store=_FakeStore(),  # type: ignore[arg-type]
    )
    assert result == "traduit"
    prompt = calls[0]["messages"][0]["content"]
    assert "page one\n\npage two" in prompt


async def test_translate_text_unknown_artifact_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _EmptyStore:
        async def get(self, artifact_id: str, token: str):
            return None

    async def fail_call_llm(**kwargs: object) -> str:
        raise AssertionError("unreachable")

    monkeypatch.setattr(translate_service, "call_llm", fail_call_llm)
    with pytest.raises(translate_service.TranslateError) as excinfo:
        await translate_service.translate_text(
            TranslationRequest(text_artifact_id="a" * 32, text_artifact_token="t" * 43),
            _settings(),
            store=_EmptyStore(),  # type: ignore[arg-type]
        )
    assert excinfo.value.status_code == 404


async def test_translate_text_missing_store_404() -> None:
    with pytest.raises(translate_service.TranslateError) as excinfo:
        await translate_service.translate_text(
            TranslationRequest(text_artifact_id="a" * 32, text_artifact_token="t" * 43),
            _settings(),
        )
    assert excinfo.value.status_code == 404
    assert excinfo.value.error == "not_found"


# ---------------------------------------------------------------------------
# TranslationServiceImpl: submit / run_translate_job / job_status
# ---------------------------------------------------------------------------


class _FakeJobQueue:
    """Records submissions; no worker. status() reads the record we plant."""

    def __init__(self) -> None:
        self.records: dict[str, Any] = {}

    async def submit(self, request: Any, *, request_meta: dict | None = None):
        from omniscribe.plugins.jobs import JobHandle

        job_id = uuid.uuid4().hex
        self.records[job_id] = (request, request_meta or {})
        return JobHandle(job_id=job_id, status_url=f"/api/jobs/{job_id}/status")

    async def status(self, job_id: str):
        return self.records.get(job_id)


class _FakeStore:
    """Artifact store double keyed by id → (token, blob, content_type)."""

    def __init__(self) -> None:
        self.blobs: dict[str, tuple[str, bytes, str]] = {}

    async def put(
        self,
        blob: bytes,
        *,
        content_type: str,
        owner_job_id: str,
        ttl_seconds: int | None = None,
    ):
        artifact_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        self.blobs[artifact_id] = (token, blob, content_type)
        # The real ArtifactStore returns an ArtifactHandle; match its
        # .id/.token attribute contract.
        return types.SimpleNamespace(id=artifact_id, token=token)

    async def get(self, artifact_id: str, token: str):
        entry = self.blobs.get(artifact_id)
        if entry is None or entry[0] != token:
            return None

        class _Blob:
            blob = entry[1]  # type: ignore[index]
            content_type = entry[2]  # type: ignore[index]

        return _Blob()


def _service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    llm_payload: str = "traduit",
    queue: _FakeJobQueue | None = None,
    store: _FakeStore | None = None,
) -> tuple[
    translate_service.TranslationServiceImpl,
    _FakeJobQueue,
    _FakeStore,
    list[dict],
]:
    calls: list[dict] = []
    _stub_llm(monkeypatch, llm_payload, calls)
    q = queue or _FakeJobQueue()
    s = store or _FakeStore()
    impl = translate_service.TranslationServiceImpl(
        _settings(), q, s, max_buffered_jobs=16
    )
    return impl, q, s, calls


def _async_request() -> AsyncTranslationRequest:
    return AsyncTranslationRequest(
        text_artifact_id="a" * 32, text_artifact_token="t" * 43
    )


async def test_run_translate_job_translates_tree_and_stores_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl, _q, store, calls = _service(monkeypatch)
    artifact_id = uuid.uuid4().hex
    store.blobs[artifact_id] = (
        "t" * 43,
        json.dumps({"0": "Hello world.", "1": "Second page."}).encode("utf-8"),
        "application/json",
    )
    # Point the payload at the seeded artifact.
    payload = translate_service._TranslatePayload(
        submission_id="s-1",
        request=AsyncTranslationRequest(
            text_artifact_id=artifact_id, text_artifact_token="t" * 43
        ),
    )

    outcome = await impl.run_translate_job(payload)

    assert outcome.content_type == "application/json"
    summary = json.loads(outcome.blob)
    assert summary["artifact_id"] == artifact_id
    assert summary["page_count"] == 2
    assert summary["blocks_translated"] >= 2
    translated_id = summary["translated_artifact_id"]
    assert translated_id in store.blobs
    # The status result must never carry the translated artifact token.
    assert "translated_artifact_token" not in summary
    translated_blob = store.blobs[translated_id][1]
    assert "traduit" in translated_blob.decode("utf-8")
    assert calls, "translator hook must reach call_llm"


async def test_run_translate_job_missing_artifact_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl, _q, _store, _calls = _service(monkeypatch)
    payload = translate_service._TranslatePayload(
        submission_id="s-1", request=_async_request()
    )
    with pytest.raises(FileNotFoundError):
        await impl.run_translate_job(payload)


async def test_run_translate_job_rejects_foreign_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl, _q, _store, _calls = _service(monkeypatch)
    with pytest.raises(ValueError):
        await impl.run_translate_job(object())


async def test_job_status_maps_all_queue_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    impl, _q, _store, _calls = _service(monkeypatch)
    from dataclasses import replace as dc_replace

    from omniscribe.plugins.state_backend import JobRecord

    base = JobRecord(job_id="j-1", status="queued")
    assert await impl.job_status("missing") is None

    queued = impl.job_status_sync(base)
    assert queued == {"job_id": "j-1", "state": "PENDING", "status": "Pending..."}

    running = impl.job_status_sync(dc_replace(base, status="running"))
    assert running["state"] == "PROGRESS"

    error = impl.job_status_sync(dc_replace(base, status="error", error="boom"))
    assert error["state"] == "FAILURE"
    assert error["error"] == "internal_error"
    # The record's exception text must not leak.
    assert "boom" not in json.dumps(error)

    cancelled = impl.job_status_sync(dc_replace(base, status="cancelled"))
    assert cancelled["state"] == "FAILURE"
    assert cancelled["error"] == "cancelled"

    complete_record = dc_replace(
        base,
        status="complete",
        result_artifact_id="r-1",
        result_artifact_token="rt",
    )
    store_blob = json.dumps({"page_count": 1}).encode("utf-8")
    impl._store.blobs["r-1"] = ("rt", store_blob, "application/json")
    # JobRecord carries a dict field and is unhashable, so the fake queue
    # is keyed by job_id: plant the record, then poll by id.
    impl._queue.records[complete_record.job_id] = complete_record
    complete = await impl.job_status(complete_record.job_id)
    assert complete is not None
    assert complete["state"] == "SUCCESS"
    assert complete["result"] == {"page_count": 1}


# ---------------------------------------------------------------------------
# Judge loop on the sync path (LLM-remediation wave)
# ---------------------------------------------------------------------------


async def test_translate_text_judge_retry_keeps_best(monkeypatch) -> None:
    translate_service._translation_cache.clear()
    calls: list[dict] = []

    async def fake_call_llm(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        if kwargs.get("system_prompt") == translate_service.EVALUATION_SYSTEM_MESSAGE:
            # First judge call rejects, second accepts.
            if (
                sum(
                    1
                    for c in calls
                    if c.get("system_prompt")
                    == translate_service.EVALUATION_SYSTEM_MESSAGE
                )
                == 1
            ):
                return '{"score": 0.2, "feedback": "wrong term", "issues": []}'
            return '{"score": 0.95, "feedback": "good", "issues": []}'
        if "Feedback:" in str(kwargs.get("messages", "")):
            return "bonne traduction"
        return "mauvaise traduction"

    monkeypatch.setattr(translate_service, "call_llm", fake_call_llm)
    result = await translate_service.translate_text(
        TranslationRequest(text="Hello world", target_language="French"),
        _settings(),
    )
    assert result == "bonne traduction"
    assert len(calls) == 4  # translate, judge, retry-translate, judge


async def test_translate_text_judge_disabled_single_call(monkeypatch) -> None:
    translate_service._translation_cache.clear()
    monkeypatch.setenv("OMNISCRIBE_TRANSLATION_EVALUATE", "false")
    calls: list[dict] = []

    async def fake_call_llm(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        return "Bonjour"

    monkeypatch.setattr(translate_service, "call_llm", fake_call_llm)
    result = await translate_service.translate_text(
        TranslationRequest(text="JudgeDisabled text", target_language="French"),
        _settings(),
    )
    assert result == "Bonjour"
    assert len(calls) == 1


async def test_translate_text_judge_outage_never_fails_request(monkeypatch) -> None:
    translate_service._translation_cache.clear()
    calls: list[dict] = []

    async def fake_call_llm(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        if kwargs.get("system_prompt") == translate_service.EVALUATION_SYSTEM_MESSAGE:
            raise RuntimeError("judge endpoint down")
        return "Bonjour"

    monkeypatch.setattr(translate_service, "call_llm", fake_call_llm)
    result = await translate_service.translate_text(
        TranslationRequest(text="Judge outage text", target_language="French"),
        _settings(),
    )
    assert result == "Bonjour"


async def test_translate_text_translates_max_tokens_passed(monkeypatch) -> None:
    translate_service._translation_cache.clear()
    calls: list[dict] = []

    async def fake_call_llm(**kwargs: object) -> str:
        calls.append(dict(kwargs))
        return "Bonjour"

    monkeypatch.setattr(translate_service, "call_llm", fake_call_llm)
    await translate_service.translate_text(
        TranslationRequest(text="Max tokens check", target_language="French"),
        _settings(),
    )
    assert calls[0]["max_tokens"] == 2048
