"""Unit tests for OCRProcessor prompt/parsing concerns (no LLM calls)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omniscribe.core.ocr import (
    CROP_PROMPT,
    OLMOCR_PAGE_PROMPT,
    LLMCallError,
    ModelNotLoadedError,
    OCRProcessor,
    _strip_runaway_repetition,
    _strip_yaml_front_matter,
)
from omniscribe.core.ocr.prompts import (
    DUAL_ENGINE_OCR_SYSTEM_MESSAGE,
    HANDWRITING_OCR_SYSTEM_MESSAGE,
    OCR_SYSTEM_MESSAGE,
    PROMPT_VERSION,
    model_supports_system_role,
    select_system_message,
)


class TestYAMLFrontMatter:
    def test_strips_canonical_olmocr_response(self):
        response = (
            "---\n"
            "primary_language: en\n"
            "is_rotation_valid: true\n"
            "rotation_correction: 0\n"
            "is_table: false\n"
            "is_diagram: false\n"
            "---\n"
            "# Document Title\n\nBody paragraph text.\n"
        )
        body = _strip_yaml_front_matter(response)
        assert "primary_language" not in body
        assert "Body paragraph text" in body
        assert body.startswith("# Document Title")

    def test_passthrough_when_no_front_matter(self):
        response = "Plain text response with no YAML.\nSecond line."
        assert _strip_yaml_front_matter(response) == response

    def test_malformed_front_matter_returned_unchanged(self):
        # Opening fence but no closing fence — don't guess; preserve text.
        response = "---\nprimary_language: en\nbody text with no closing fence"
        assert _strip_yaml_front_matter(response) == response

    def test_leading_whitespace_handled(self):
        response = "  \n---\nkey: val\n---\nbody"
        out = _strip_yaml_front_matter(response)
        assert out == "body"


class TestStripRunawayRepetition:
    def test_passes_short_unique_lines_through(self):
        lines = ["a", "b", "c", "d"]
        assert _strip_runaway_repetition(lines) == lines

    def test_admits_legitimate_table_repetition(self):
        # HTML table from OlmOCR's "tables to HTML" instruction — many <tr>
        # tags are expected and shouldn't be clipped.
        lines = ["<table>"] + ["<tr>", "<td>x</td>", "</tr>"] * 10 + ["</table>"]
        out = _strip_runaway_repetition(lines, max_repeat=20)
        assert out == lines, "10x table repetition must survive"

    def test_clips_runaway_repetition(self):
        # Model stuck in a loop emitting the same line 100 times — should
        # leave only the first 20 occurrences.
        lines = ["unique header"] + ["LOOP"] * 100 + ["unique footer"]
        out = _strip_runaway_repetition(lines, max_repeat=20)
        assert out.count("LOOP") == 20
        assert out[0] == "unique header"
        # The repetition cap applies only to the repeated "LOOP" line;
        # unrelated later lines (the footer) are still preserved.
        assert "unique footer" in out

    def test_clips_multiple_runaway_lines_independently(self):
        lines = ["A"] * 50 + ["B"] * 50
        out = _strip_runaway_repetition(lines, max_repeat=10)
        assert out.count("A") == 10
        assert out.count("B") == 10

    def test_empty_input(self):
        assert _strip_runaway_repetition([]) == []


class TestHallucinationFilter:
    async def test_pangram_response_treated_as_blank(self):
        """OlmOCR-2 falls back to 'The quick brown fox...' on blank/unreadable
        crops. perform_ocr_on_crop must drop those instead of placing them
        in the searchable text layer."""
        # Sprint 4 / M-6 audit fix: convert this test to ``async def``
        # so the OCRProcessor runs on the suite's event loop instead of
        # spawning a fresh loop via ``asyncio.run``. pytest-asyncio's
        # auto mode (configured in pyproject.toml) drives the
        # coroutine without an explicit marker.
        ocr = OCRProcessor.__new__(OCRProcessor)  # skip real init
        ocr.client = None  # never used; we override _chat below
        ocr.model = "qwen/qwen3-vl-8b"  # non-OlmOCR — exercise the system message path

        async def _fake_pangram(*a, **kw):
            return "The quick brown fox jumps over the lazy dog."

        ocr._chat = _fake_pangram  # type: ignore[method-assign]
        ocr.CROP_TIMEOUT_S = 60.0
        ocr.CROP_MAX_TOKENS = 256
        result = await ocr.perform_ocr_on_crop("ignored")
        assert result == ""

    async def test_normal_crop_response_passes_through(self):
        ocr = OCRProcessor.__new__(OCRProcessor)
        ocr.client = None
        ocr.model = "qwen/qwen3-vl-8b"

        async def _fake(*a, **kw):
            return "real handwritten content"

        ocr._chat = _fake  # type: ignore[method-assign]
        ocr.CROP_TIMEOUT_S = 60.0
        ocr.CROP_MAX_TOKENS = 256
        assert await ocr.perform_ocr_on_crop("ignored") == "real handwritten content"

    async def test_real_text_containing_pangram_is_preserved(self):
        # A document that legitimately contains the pangram (e.g. a typing
        # exercise) must NOT be silently dropped. The filter only fires
        # when the response IS the pangram, not when it merely contains it.
        ocr = OCRProcessor.__new__(OCRProcessor)
        ocr.client = None
        ocr.model = "qwen/qwen3-vl-8b"

        sentence = (
            "Practice typing: The quick brown fox jumps over the lazy dog. "
            "Repeat ten times."
        )

        async def _fake(*a, **kw):
            return sentence

        ocr._chat = _fake  # type: ignore[method-assign]
        ocr.CROP_TIMEOUT_S = 60.0
        ocr.CROP_MAX_TOKENS = 256
        assert await ocr.perform_ocr_on_crop("ignored") == sentence

    async def test_pangram_with_quotes_or_trailing_punct_still_dropped(self):
        # OlmOCR sometimes wraps the pangram in quotes or appends ! / ? —
        # normalization must still recognise it as the fallback.
        ocr = OCRProcessor.__new__(OCRProcessor)
        ocr.client = None
        ocr.model = "qwen/qwen3-vl-8b"

        def _make_fake(response: str):
            async def _fake(*a, **kw):
                return response

            return _fake

        for variant in (
            "The quick brown fox jumps over the lazy dog!",
            '"The quick brown fox jumps over the lazy dog."',
            "the quick brown fox jumps over the lazy dog",
        ):
            ocr = OCRProcessor.__new__(OCRProcessor)
            ocr.client = None
            ocr.model = "qwen/qwen3-vl-8b"
            ocr._chat = _make_fake(variant)  # type: ignore[method-assign]
            ocr.CROP_TIMEOUT_S = 60.0
            ocr.CROP_MAX_TOKENS = 256
            assert await ocr.perform_ocr_on_crop("ignored") == "", (
                f"variant {variant!r} should be dropped"
            )


def _fake_models_client(model_ids=None, raise_exc=None):
    """Build a stand-in for AsyncOpenAI exposing only `client.models.list()`.

    Mirrors the SDK shape: ``await client.models.list()`` returns an object
    with a ``.data`` attribute that's a list of objects each with an ``.id``.
    """

    async def _list():
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(data=[SimpleNamespace(id=m) for m in (model_ids or [])])

    return SimpleNamespace(models=SimpleNamespace(list=_list))


def _make_ocr_with_fake_client(model: str, fake_client) -> OCRProcessor:
    """Construct an OCRProcessor without going through __init__ (which
    would create a real AsyncOpenAI client). Same trick as
    TestHallucinationFilter above."""
    ocr = OCRProcessor.__new__(OCRProcessor)
    ocr.api_base = "http://localhost:1234/v1"
    ocr.model = model
    ocr.client = fake_client
    return ocr


class TestEnsureModelLoaded:
    """Pre-flight check that the requested model is loaded on the LLM
    server. LM Studio silently falls back to whatever is loaded on
    mismatch, so without this check users get bad OCR with no error
    (issue #7)."""

    # Sprint 4 / M-6 audit fix: the model-load pre-flight is async at
    # heart; rewriting the tests as ``async def`` lets them share the
    # suite's event loop and removes the per-test ``asyncio.run`` hop.

    async def test_passes_when_model_in_loaded_list(self):
        ocr = _make_ocr_with_fake_client(
            "qwen/qwen3-vl-8b",
            _fake_models_client(["qwen/qwen3-vl-8b", "allenai/olmocr-2-7b"]),
        )
        # No raise — exact match found.
        await ocr.ensure_model_loaded()

    async def test_passes_case_insensitive(self):
        # User passes "Qwen/Qwen3-VL-8B" but server returns "qwen/qwen3-vl-8b"
        # — same model file, just shifted case. Don't make the user fight casing.
        ocr = _make_ocr_with_fake_client(
            "Qwen/Qwen3-VL-8B",
            _fake_models_client(["qwen/qwen3-vl-8b"]),
        )
        await ocr.ensure_model_loaded()

    async def test_passes_repo_prefix_and_tag_tolerant(self):
        # User passes "olmocr-2-7b" but LM Studio loaded "allenai/olmocr-2-7b"
        ocr = _make_ocr_with_fake_client(
            "olmocr-2-7b",
            _fake_models_client(["allenai/olmocr-2-7b"]),
        )
        await ocr.ensure_model_loaded()

        # User passes "allenai/olmocr-2-7b:latest"
        ocr_tagged = _make_ocr_with_fake_client(
            "allenai/olmocr-2-7b:latest",
            _fake_models_client(["allenai/olmocr-2-7b"]),
        )
        await ocr_tagged.ensure_model_loaded()

    async def test_raises_with_helpful_message_on_mismatch(self):
        # The exact scenario from the issue: user passed qwen3-vl-8b but
        # LM Studio has olmocr loaded. Must surface this loudly.
        ocr = _make_ocr_with_fake_client(
            "qwen/qwen3-vl-8b",
            _fake_models_client(["allenai_olmocr-2-7b-1025"]),
        )
        with pytest.raises(ModelNotLoadedError) as exc_info:
            await ocr.ensure_model_loaded()

        msg = str(exc_info.value)
        # Must name the requested model (so the user knows what they asked for)…
        assert "qwen/qwen3-vl-8b" in msg
        # …and what the server actually has loaded (so they can either
        # change --model or load the right one)…
        assert "allenai_olmocr-2-7b-1025" in msg
        # …and tell them about the escape hatch for non-LM-Studio servers.
        assert "--no-verify-model" in msg
        # …and explain WHY this matters (silent fallback) so they don't
        # treat the check as a bug to disable and forget.
        assert "silently" in msg.lower() or "fallback" in msg.lower()

    async def test_raises_with_none_listing_when_no_models_loaded(self):
        # LM Studio with no model loaded at all. The error message
        # should still be informative, not say "Loaded models: " followed
        # by nothing (which reads like a parse error).
        ocr = _make_ocr_with_fake_client(
            "qwen/qwen3-vl-8b",
            _fake_models_client([]),
        )
        with pytest.raises(ModelNotLoadedError) as exc_info:
            await ocr.ensure_model_loaded()
        assert "(none)" in str(exc_info.value)

    def test_subclass_of_llm_call_error(self):
        # Existing callers of LLMCallError (e.g. CLI's generic
        # except-and-print path) must continue to catch this without
        # special-casing.
        assert issubclass(ModelNotLoadedError, LLMCallError)

    async def test_server_failure_wrapped_as_llm_call_error(self):
        # If /v1/models fails (server down, wrong endpoint, auth error)
        # surface a single-paragraph LLMCallError rather than the bare
        # ConnectionError stack — match the diagnostic style of _chat.
        ocr = _make_ocr_with_fake_client(
            "qwen/qwen3-vl-8b",
            _fake_models_client(raise_exc=ConnectionError("connection refused")),
        )
        with pytest.raises(LLMCallError) as exc_info:
            await ocr.ensure_model_loaded()
        # Must point the user at the server (where to look) and at the
        # opt-out flag (how to bypass for non-conforming servers).
        assert "http://localhost:1234/v1" in str(exc_info.value)
        assert "--no-verify-model" in str(exc_info.value)


class TestPromptConstants:
    def test_olmocr_prompt_is_canonical(self):
        # Guard against accidental prompt drift — this string was lifted
        # verbatim from allenai/olmocr. If you change it, expect worse OCR.
        assert "Attached is one page of a document" in OLMOCR_PAGE_PROMPT
        assert "Convert equations to LateX and tables to HTML" in OLMOCR_PAGE_PROMPT
        assert "front matter section" in OLMOCR_PAGE_PROMPT

    def test_crop_prompt_is_minimal(self):
        # For crops we want plain text — no metadata/markdown ceremony.
        assert "no markdown" in CROP_PROMPT.lower()
        assert "plain text" in CROP_PROMPT.lower()

    def test_prompt_version_is_present(self):
        # Bump PROMPT_VERSION when any user-facing prompt body changes so
        # log / runtime telemetry can correlate regressions with a known
        # version. The exact date is not asserted, just the format.
        assert isinstance(PROMPT_VERSION, str)
        assert PROMPT_VERSION  # non-empty
        # date.version format: YYYY-MM-DD.vN
        assert PROMPT_VERSION.count(".") >= 1
        date_part = PROMPT_VERSION.split(".")[0]
        assert len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-"

    def test_ocr_system_message_guards_against_invented_text(self):
        # The crop / page prompts rely on the system message to enforce
        # the "no invented text" rule. If this assertion ever fails,
        # downstream extraction / alignment will start seeing
        # hallucinated content on blank regions.
        assert "invent" in OCR_SYSTEM_MESSAGE.lower()
        assert (
            "diacritical" in OCR_SYSTEM_MESSAGE.lower()
            or "diacritics" in OCR_SYSTEM_MESSAGE.lower()
        )

    def test_handwriting_system_message_patience_emphasis(self):
        # Handwriting mode should reinforce "return empty rather than
        # guess" — the base OCR message says the same but handwriting
        # benefits from making this explicit and prominent.
        assert "empty" in HANDWRITING_OCR_SYSTEM_MESSAGE.lower()
        assert "handwritten" in HANDWRITING_OCR_SYSTEM_MESSAGE.lower()

    def test_dual_engine_system_message_uses_image_as_truth(self):
        # The dual-engine path must tell the model the image is the
        # source of truth and the draft is a hint, otherwise the model
        # will parrot the Tesseract draft even where it is wrong.
        assert "image" in DUAL_ENGINE_OCR_SYSTEM_MESSAGE.lower()
        assert "draft" in DUAL_ENGINE_OCR_SYSTEM_MESSAGE.lower()
        assert "source of truth" in DUAL_ENGINE_OCR_SYSTEM_MESSAGE.lower()


class TestSelectSystemMessage:
    def test_dual_engine_path_uses_dual_engine_message(self):
        result = select_system_message(handwriting_mode=False, dual_engine=True)
        assert result is DUAL_ENGINE_OCR_SYSTEM_MESSAGE

    def test_handwriting_path_uses_handwriting_message(self):
        result = select_system_message(handwriting_mode=True, dual_engine=False)
        assert result is HANDWRITING_OCR_SYSTEM_MESSAGE

    def test_default_path_uses_ocr_message(self):
        result = select_system_message(handwriting_mode=False, dual_engine=False)
        assert result is OCR_SYSTEM_MESSAGE

    def test_dual_engine_overrides_handwriting(self):
        # When both flags are on, dual_engine wins — it's the more
        # specific role (judge, not transcriber).
        result = select_system_message(handwriting_mode=True, dual_engine=True)
        assert result is DUAL_ENGINE_OCR_SYSTEM_MESSAGE


class TestModelSupportsSystemRole:
    """Models that have demonstrated system-role sensitivity get a
    shorter, model-aware path that drops the system message entirely.
    The list is intentionally narrow — see the docstring in
    :func:`model_supports_system_role`.
    """

    def test_olmocr_2_model_is_excluded(self):
        assert model_supports_system_role("allenai/olmocr-2-7b") is False

    def test_olmocr_short_name_is_excluded(self):
        assert model_supports_system_role("olmocr") is False

    def test_olmocr_case_insensitive(self):
        # The check is case-insensitive — model names from LM Studio
        # sometimes carry the original casing, sometimes lowercase.
        assert model_supports_system_role("AllenAI/OLMOCR-2-7B") is False

    def test_qwen_model_supports_system_role(self):
        assert model_supports_system_role("qwen/qwen3-vl-8b") is True

    def test_unknown_model_defaults_to_supported(self):
        # New model we haven't seen — don't accidentally disable
        # system messages. Better to have a regression elsewhere
        # than to silently strip the system role.
        assert model_supports_system_role("some/new-model-v1") is True

    def test_none_model_defaults_to_supported(self):
        # Defensive: a misconfigured processor with no model should
        # still attempt a system message rather than skip it.
        assert model_supports_system_role(None) is True


class TestProcessorSystemPromptWiring:
    """The OCR processor decides at the call site whether to send a
    system message. Two reasons it returns ``None``:

    1. The canonical OLMOCR-2 page prompt is in use — the model was
       RL-trained on the prompt as a pure user message, so the
       processor must NOT add a system role there.
    2. The active model is in the system-role-excluded family
       (currently OlmOCR). Sending a system role causes LM Studio
       + OlmOCR-2 to misbehave on the crop / handwriting / dual-engine
       paths.

    The tests below cover both reasons separately.
    """

    def _make_processor(self, model: str = "qwen/qwen3-vl-8b") -> OCRProcessor:
        # No real network — build a processor with the minimum viable
        # config and stub the chat method on the instance. Default
        # model is a non-OlmOCR one so we exercise the system-message
        # path; OlmOCR-specific cases have their own tests below.
        return OCRProcessor(api_base="http://localhost:1/v1", api_key="x", model=model)

    async def test_olmocr_page_path_sends_no_system_message(self):
        # Qwen model + OLMOCR canonical page prompt → no system message
        # because the prompt itself is the RL-trained distribution.
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        captured: dict = {}

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured["system_prompt"] = system_prompt
            return "# Page"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr(image_base64="aW1hZ2U=")
        assert captured["system_prompt"] is None

    async def test_handwriting_page_path_sends_handwriting_system_message(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        proc.handwriting_mode = True
        captured: dict = {}

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured["system_prompt"] = system_prompt
            return "# Page"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr(image_base64="aW1hZ2U=")
        assert captured["system_prompt"] is HANDWRITING_OCR_SYSTEM_MESSAGE

    async def test_crop_path_sends_ocr_system_message(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        captured: dict = {}

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured["system_prompt"] = system_prompt
            return "text"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr_on_crop(image_base64="aW1hZ2U=")
        assert captured["system_prompt"] is OCR_SYSTEM_MESSAGE

    async def test_crop_repair_hint_appended_and_default_temperature_kept(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        captured: dict = {}

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            captured["prompt"] = prompt
            captured["temperature"] = temperature
            return "fixed text"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr_on_crop(
            image_base64="aW1hZ2U=",
            repair_hint="REPAIR PASS 2: your previous reading of this region was:\nwrong text",
        )
        assert "REPAIR PASS 2" in captured["prompt"]
        assert "wrong text" in captured["prompt"]
        assert captured["temperature"] is None

    async def test_crop_repair_temperature_override_reaches_chat(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        captured: dict = {}

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            captured["temperature"] = temperature
            return "fixed text"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr_on_crop(
            image_base64="aW1hZ2U=", repair_hint="hint", temperature=0.2
        )
        assert captured["temperature"] == 0.2

    async def test_page_correction_empty_keeps_first_pass(self):
        """An empty correction pass must not erase a valid first pass."""
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        responses = ["First pass text", ""]

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            return responses.pop(0)

        proc._chat = fake_chat  # type: ignore[method-assign]
        lines = await proc.perform_ocr(image_base64="aW1hZ2U=", self_correction=True)
        assert lines == ["First pass text"]

    async def test_page_correction_fallback_keeps_first_pass(self):
        """A fallback-response correction (pangram) keeps the first pass."""
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        responses = [
            "First pass text",
            "The quick brown fox jumps over the lazy dog.",
        ]

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            return responses.pop(0)

        proc._chat = fake_chat  # type: ignore[method-assign]
        lines = await proc.perform_ocr(image_base64="aW1hZ2U=", self_correction=True)
        assert lines == ["First pass text"]

    async def test_page_correction_valid_result_replaces_first_pass(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        responses = ["First pass text", "Corrected page text"]

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            return responses.pop(0)

        proc._chat = fake_chat  # type: ignore[method-assign]
        lines = await proc.perform_ocr(image_base64="aW1hZ2U=", self_correction=True)
        assert lines == ["Corrected page text"]

    async def test_crop_correction_fallback_keeps_first_pass(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        responses = [
            "first crop text",
            "The quick brown fox jumps over the lazy dog.",
        ]

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            return responses.pop(0)

        proc._chat = fake_chat  # type: ignore[method-assign]
        result = await proc.perform_ocr_on_crop(
            image_base64="aW1hZ2U=", self_correction=True
        )
        assert result == "first crop text"

    async def test_crop_correction_empty_keeps_first_pass(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        responses = ["first crop text", ""]

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            return responses.pop(0)

        proc._chat = fake_chat  # type: ignore[method-assign]
        result = await proc.perform_ocr_on_crop(
            image_base64="aW1hZ2U=", self_correction=True
        )
        assert result == "first crop text"

    async def test_crop_correction_valid_result_replaces_first_pass(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        responses = ["first crop text", "corrected crop text"]

        async def fake_chat(
            prompt,
            image_base64,
            *,
            timeout,
            max_tokens,
            system_prompt=None,
            temperature=None,
        ):
            return responses.pop(0)

        proc._chat = fake_chat  # type: ignore[method-assign]
        result = await proc.perform_ocr_on_crop(
            image_base64="aW1hZ2U=", self_correction=True
        )
        assert result == "corrected crop text"

    def test_prompt_versions_are_independent_constants(self) -> None:
        """Same value today, independent names so future bumps don't collide."""
        from omniscribe.core.grounded import prompted
        from omniscribe.core.translate import nodes

        assert prompted.GROUNDED_PROMPT_VERSION == PROMPT_VERSION
        assert nodes.TRANSLATION_PROMPT_VERSION == PROMPT_VERSION

    async def test_dual_engine_crop_path_sends_dual_engine_system_message(self):
        proc = self._make_processor(model="qwen/qwen3-vl-8b")
        captured: list = []

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured.append(system_prompt)
            return "text"

        proc._chat = fake_chat  # type: ignore[method-assign]
        # No Tesseract available in the test env, so dual_engine is
        # effectively a no-op for the draft. The system message is
        # still set, and the call returns the result of fake_chat.
        await proc.perform_ocr_on_crop(image_base64="aW1hZ2U=", dual_engine=True)
        assert all(sp is DUAL_ENGINE_OCR_SYSTEM_MESSAGE for sp in captured)
        assert captured  # at least one chat call happened

    async def test_olmocr_model_drops_system_message_on_handwriting_path(self):
        # OlmOCR-2 + handwriting mode → no system message. This is
        # the bug from the field report: LM Studio + OlmOCR-2 fails
        # on the crop / handwriting / dual-engine paths when a
        # system role is layered on top of the model's RL training.
        proc = self._make_processor(model="allenai/olmocr-2-7b")
        proc.handwriting_mode = True
        captured: dict = {}

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured["system_prompt"] = system_prompt
            return "# Page"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr(image_base64="aW1hZ2U=")
        assert captured["system_prompt"] is None

    async def test_olmocr_model_drops_system_message_on_crop_path(self):
        proc = self._make_processor(model="allenai/olmocr-2-7b")
        captured: dict = {}

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured["system_prompt"] = system_prompt
            return "text"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr_on_crop(image_base64="aW1hZ2U=")
        assert captured["system_prompt"] is None

    async def test_olmocr_model_drops_system_message_on_dual_engine_path(self):
        proc = self._make_processor(model="allenai/olmocr-2-7b")
        captured: list = []

        async def fake_chat(
            prompt, image_base64, *, timeout, max_tokens, system_prompt=None
        ):
            captured.append(system_prompt)
            return "text"

        proc._chat = fake_chat  # type: ignore[method-assign]
        await proc.perform_ocr_on_crop(image_base64="aW1hZ2U=", dual_engine=True)
        assert all(sp is None for sp in captured)
        assert captured  # at least one chat call happened


class TestChatRetrySingleLayer:
    """F1.2 audit fix (P0): single retry layer for the OCR pipeline.

    The previous design had two independent retry loops — one in
    ``OCRProcessor._chat`` (outer, ``MAX_RETRIES + 1``) and one in
    ``complete_vlm_prompt`` (inner, env-driven ``OMNISCRIBE_LLM_MAX_RETRIES``).
    Worst-case they multiplied to ``(MAX_RETRIES+1) * (max_retries+1)`` VLM
    calls per page on a dead endpoint (default 3 * 3 = 9).

    After the fix, ``complete_vlm_prompt`` defaults to ``max_retries=0``
    (single POST). ``OCRProcessor._chat`` is the single retry authority
    for the OCR pipeline. Total VLM calls = ``MAX_RETRIES + 1`` (default 3).
    """

    async def test_chat_does_not_multiply_retries(self) -> None:
        """End-to-end: mock at the httpx layer and count VLM POSTs.

        With the layered-retry bug, this test would observe 9 calls
        (3 outer × 3 inner) for a transient 503; the fix yields 3.
        """
        from unittest.mock import patch

        import httpx

        from omniscribe.core.ocr import LLMCallError

        proc = OCRProcessor(
            api_base="http://localhost:1/v1", api_key="x", model="qwen/qwen3-vl-8b"
        )
        # Keep the backoff loop fast — the test asserts the *count*, not
        # the timing.
        proc.RETRY_BASE_DELAY_S = 0.001

        call_count = 0

        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return httpx.Response(503, text="Service Unavailable")

        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            with pytest.raises(LLMCallError):
                await proc._chat(
                    prompt="OCR this",
                    image_base64="aW1hZ2U=",
                    timeout=60.0,
                    max_tokens=4096,
                )

        expected = proc.MAX_RETRIES + 1
        assert call_count == expected, (
            f"Expected {expected} VLM calls (MAX_RETRIES+1), got {call_count}. "
            f"Layered retries between _chat and complete_vlm_prompt "
            f"multiplied attempts (CWE-400)."
        )

    async def test_chat_sends_jpeg_data_url(self) -> None:
        from unittest.mock import AsyncMock, patch

        from omniscribe.core.ocr.chat_client import ChatClient
        from omniscribe.core.ocr.resilience import CircuitBreaker

        client = ChatClient(
            model="test-model",
            api_base="http://localhost:1234/v1",
            api_key="test-key",
            max_retries=1,
            retry_base_delay_s=0.01,
            retry_max_delay_s=0.1,
            circuit_breaker=CircuitBreaker(),
        )
        with patch(
            "omniscribe.core.ocr.chat_client.call_llm", new_callable=AsyncMock
        ) as mock_call:
            mock_call.return_value = "extracted text"
            res = await client.chat(
                prompt="read this",
                image_base64="fakeb64data",
                timeout=30.0,
                max_tokens=1000,
            )
            assert res == "extracted text"
            mock_call.assert_awaited_once()
            messages = mock_call.call_args.kwargs["messages"]
            user_content = messages[0]["content"]
            image_part = next(
                part for part in user_content if part["type"] == "image_url"
            )
            assert (
                image_part["image_url"]["url"] == "data:image/jpeg;base64,fakeb64data"
            )
