# LLM Remediation — Subsystem 2 (OCR / Trust / Transcription) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the trust layer real input (populate `DocumentBlock.confidence`), make trust actionable (API + Flutter), fix transcription confidence domain and whisper robustness, make repair retries informed instead of blind, and split the shared PROMPT_VERSION.

**Architecture:** All OCR paths converge on `EngineBase` → `DocumentResult.from_pages_data` (`workflows/base.py:395`) — that's the single confidence-population choke point. The repair loop (`workflows/repair.py`) gains context-passing re-OCR (`previous_text` + `attempt`); both engines supply informed prompts. Trust outputs flow to `plugins/documents/schemas.py` and the Flutter export modal. Transcription fixes are engine-local.

**Tech Stack:** Python 3.12, PyMuPDF, faster-whisper (mocked), httpx, pytest, Flutter/Dart for the export-modal widget.

**Spec:** `docs/superpowers/specs/2026-09-06-llm-remediation-design.md`, subsystem 2 section.

---

### Task C1: Populate `DocumentBlock.confidence` at the choke point

**Files:**
- Modify: `src/omniscribe/core/document.py:86-112` (`from_pages_data`), `src/omniscribe/core/workflows/base.py:395`
- Test: `tests/core/test_document.py` (locate existing document IR tests; else create `tests/core/test_document_confidence.py`)

- [ ] **Step 1: Failing test**

```python
def test_from_pages_data_estimates_confidence() -> None:
    from omniscribe.core.document import DocumentResult

    result = DocumentResult.from_pages_data(
        {0: [((0.0, 0.0, 1.0, 0.1), "Several well formed words here")]},
        confidence_fn=lambda t: 0.99 if len(t.split()) >= 3 else 0.4,
    )
    assert result.pages[0].blocks[0].confidence == 0.99


def test_from_pages_data_without_confidence_fn_keeps_none() -> None:
    from omniscribe.core.document import DocumentResult

    result = DocumentResult.from_pages_data(
        {0: [((0.0, 0.0, 1.0, 0.1), "text")]}
    )
    assert result.pages[0].blocks[0].confidence is None
```

- [ ] **Step 2: Run** — expect FAIL (no `confidence_fn` param).

- [ ] **Step 3: Implement.** `document.py from_pages_data` gains `confidence_fn: Callable[[str], float] | None = None` (import `Callable` from `collections.abc`); block construction sets `confidence=confidence_fn(text) if confidence_fn is not None and text.strip() else None`. In `base.py:395` pass `confidence_fn=_estimate_confidence` (import from `omniscribe.core.workflows.utils`).

- [ ] **Step 4: Run** `uv run pytest tests/core -q -k document or confidence` — PASS.
- [ ] **Step 5: Commit** `-m "ocr: populate block confidence at the from_pages_data choke point"`

### Task C2: Trust scores/flags surface in the documents API + warning gate

**Files:**
- Modify: `src/omniscribe/plugins/documents/schemas.py`, the block-serialization site in `plugins/documents/service.py` (locate `trust_score` absence via grep), `src/omniscribe/core/ocr_quality/orchestrator.py` (emit warning when block score < `trust_flag_threshold` — verify current `_compose_blocks` behavior first)
- Test: `tests/plugins/test_documents_schemas.py` (or nearest existing), `tests/core/ocr_quality/test_ocr_quality_orchestrator.py`

- [ ] **Step 1: Failing tests**

```python
def test_block_schema_carries_trust_fields() -> None:
    # Whatever pydantic/block schema the documents plugin exposes for a
    # block must include trust_score/trust_flags when populated.
    ...
```

(Write against the actual schema class found by grep `class .*Block` in `plugins/documents/schemas.py`; assert the serialized dict includes `"trust_score": 0.42` and `"trust_flags": ["LOW_CALIBRATED_CONF"]` for a block carrying them, and omits/nulls them otherwise.)

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** pass-through fields on the schema + serialization mapping.
- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "ocr-quality: trust_score/trust_flags exposed on document API blocks"`

### Task C3: Flutter export-modal trust summary

**Files:**
- Modify: `client/lib/presentation/workstation/modals/export_modal.dart` (+ model if needed), `client/test/presentation/workstation/modals/export_modal_test.dart` (or nearest widget test)
- [ ] Widget shows a compact "N blocks flagged for review" line when any block's `trust_flags` is non-empty, nothing otherwise. Widget test with a fake block list. **Commit** `-m "client: export modal trust-flag summary"`

### Task C4: Transcription confidence domain fix

**Files:**
- Modify: `src/omniscribe/core/transcription/local_engine.py:115`, `src/omniscribe/core/transcription/api_engine.py:128`, `src/omniscribe/core/transcription/types.py:28`
- Test: `tests/core/transcription/test_transcription_engines.py`

- [ ] **Step 1: Failing tests**

```python
def test_local_confidence_is_probability_not_logprob(...) -> None:
    # Fake segment avg_logprob=-0.3567 → stored confidence ≈ 0.70
    ...

def test_api_confidence_is_probability_not_logprob(...) -> None:
    # payload segment avg_logprob=-0.7 → confidence ≈ e^-0.7 ≈ 0.4966
    ...
```

(Adapt to existing engine test doubles; assert `0 <= confidence <= 1` and `confidence == pytest.approx(math.exp(logprob), abs=1e-6)`.)

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** shared helper in `types.py`:

```python
import math

def logprob_to_confidence(avg_logprob: float | None) -> float | None:
    """Convert a whisper avg_logprob (log-domain, typically negative) to a
    [0, 1] probability so downstream trust math can consume it directly."""
    if avg_logprob is None:
        return None
    return max(0.0, min(1.0, math.exp(avg_logprob)))
```

Both engines call it; `types.py` `TranscriptionSegment.confidence` docstring notes the domain.

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "transcription: confidence stores exp(avg_logprob) probability, not raw logprob"`

### Task C5: Whisper robustness config + segment joining

**Files:**
- Modify: `src/omniscribe/core/transcription/local_engine.py`
- Test: `tests/core/transcription/test_transcription_engines.py`

- [ ] **Step 1: Failing tests**

```python
def test_local_engine_passes_resilience_kwargs(fake_model_record) -> None:
    # transcribe(...) → recorded kwargs contain beam_size=5,
    # condition_on_previous_text=False, vad_filter=True,
    # and temperature is a tuple starting with the requested temperature.

def test_segments_joined_with_newlines_on_sentence_end(...) -> None:
    # Segment texts ["Hello world.", "Second part"] →
    # full text "Hello world.\nSecond part"; short fragment merged.
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

```python
    def _sync_transcribe() -> tuple[list[Any], Any]:
        segments_iter, info = model.transcribe(
            tmp_path,
            language=language,
            initial_prompt=prompt,
            temperature=(temperature, 0.2, 0.4, 0.6, 0.8, 1.0),
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=True,
            word_timestamps=True,
        )
        return list(segments_iter), info
```

Joining helper (module-level, unit-testable):

```python
_SENTENCE_END = tuple("。！？!?.…")

def _join_segment_texts(parts: list[str]) -> str:
    """Join segments; newline after sentence-final punctuation, else space."""
    out = ""
    for part in parts:
        if not part:
            continue
        if not out:
            out = part
        elif out.endswith(_SENTENCE_END):
            out += "\n" + part
        else:
            out += " " + part
    return out
```

Replace `" ".join(full_text_parts)` with `_join_segment_texts(full_text_parts)`. (Short-segment merging deferred — the VAD + beam config addresses the hallucination loop; joining restores structure.)

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "transcription: whisper beam/vad/fallback-temp config, structure-preserving segment join"`

### Task C6: API transcription retry hygiene

**Files:**
- Modify: `src/omniscribe/core/transcription/api_engine.py:65-111`
- Test: `tests/core/transcription/test_transcription_engines.py`

- [ ] **Step 1: Failing tests**

```python
def test_api_retry_hoists_client_and_backs_off(monkeypatch) -> None:
    # Fake httpx.AsyncClient records how many times it was constructed
    # (must be 1 across 3 attempts) and returns 500, 429 (+Retry-After: 7),
    # then 200. Patch asyncio.sleep to record delays — the 429 delay must
    # be ≥ 7 (Retry-After honored), transient delay grows exponentially.
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

```python
        max_attempts = 3
        last_exception: Exception | None = None

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.post(url, headers=headers, data=data, files=files)
                except TranscriptionError:
                    raise
                except Exception as exc:
                    last_exception = exc
                    if is_transient_error(exc) and attempt < max_attempts:
                        delay = self._retry_delay_s(attempt, None)
                        logger.warning("Transient audio API error (attempt %d/%d): %s", attempt, max_attempts, exc)
                        await asyncio.sleep(delay)
                        continue
                    break
                if response.status_code == 200:
                    return self._parse_verbose_json(response.json())
                if response.status_code in (401, 403):
                    raise TranscriptionError("Invalid API key or unauthorized access.", status_code=response.status_code)
                if response.status_code == 404:
                    raise TranscriptionError(f"Model or endpoint not found: {self.model}", status_code=404)
                retry_after = response.headers.get("Retry-After")
                last_exception = TranscriptionError(
                    f"Audio API transcription failed with status {response.status_code}: {response.text}",
                    status_code=response.status_code,
                )
                if attempt < max_attempts and (response.status_code in (429, 500, 502, 503, 504) or is_transient_error(last_exception)):
                    await asyncio.sleep(self._retry_delay_s(attempt, retry_after))
                    continue
                break

        raise TranscriptionError(
            f"Audio transcription API request failed: {last_exception}", status_code=502
        ) from last_exception

    @staticmethod
    def _retry_delay_s(attempt: int, retry_after: str | None) -> float:
        """Exponential backoff (1/2/4s base, 16s cap); Retry-After wins."""
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(1.0 * (2 ** (attempt - 1)), 16.0)
```

Hoist `import asyncio` to module top. Behavior preserved: non-transient status codes raise as before (the TranscriptionError raised inside the loop still propagates).

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "transcription: hoisted client, exponential backoff, Retry-After honored"`

### Task C7: Informed repair re-OCR (previous attempt + reason, temperature bump)

**Files:**
- Modify: `src/omniscribe/core/workflows/repair.py` (`ReOcrBlock`, `repair_page`), `src/omniscribe/core/workflows/hybrid_repair.py:186-212`, `src/omniscribe/core/workflows/grounded.py:307-333`, `src/omniscribe/core/grounded/prompted.py` (`ocr_crop`, `REPAIR_CROP_PROMPT`), `src/omniscribe/core/ocr/processor.py` (`perform_ocr_on_crop` gains `repair_hint`)
- Test: `tests/core/workflows/test_repair.py`, `tests/core/ocr/test_ocr_processor.py` (nearest)

- [ ] **Step 1: Failing tests**

```python
async def test_repair_passes_previous_text_and_attempt() -> None:
    # re_ocr double records (block_idx, bbox, previous_text, attempt);
    # two-retry flow shows attempt increasing and previous_text updating.

async def test_perform_ocr_on_crop_appends_repair_hint(...) -> None:
    # _chat recorder asserts the crop prompt ends with the repair hint
    # naming the previous attempt.

def test_repair_crop_prompt_includes_previous_text_and_reason() -> None:
    assert "previous attempt" in REPAIR_CROP_PROMPT.lower()
    assert "{previous_text}" in REPAIR_CROP_PROMPT
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

`repair.py`:

```python
ReOcrBlock = Callable[..., Awaitable[str]]
```
with the documented contract: engines must accept
`(block_idx, bbox, *, previous_text: str = "", attempt: int = 1)`.
`repair_page` calls `re_ocr(block_idx, bbox, previous_text=text, attempt=attempt)`.

`hybrid_repair.py re_ocr`:

```python
    async def re_ocr(
        block_idx: int,
        bbox: tuple[float, float, float, float],
        *,
        previous_text: str = "",
        attempt: int = 1,
    ) -> str:
        ...
        hint = (
            f"\nREPAIR PASS {attempt}: your previous reading of this region was:\n"
            f"{previous_text}\nIt was rejected as unreliable (confidence below "
            "target or garbled). Re-read the crop carefully; keep every line, "
            "fix misreads, do not add commentary."
            if previous_text else ""
        )
        text = await engine.ocr_processor.perform_ocr_on_crop(
            crop_b64, repair_hint=hint or None,
            temperature=0.1 + 0.1 * (attempt - 1) if attempt > 1 else None,
        )
```

`processor.py perform_ocr_on_crop` gains `repair_hint: str | None = None, temperature: float | None = None`; both thread into `self._chat(prompt + (sanitize_prompt_input(repair_hint) if repair_hint else ""), ..., temperature=temperature if temperature is not None else self.crop_temperature)` (locate the crop temperature constant in the file and preserve default).

`grounded/prompted.py`:

```python
REPAIR_CROP_PROMPT = (
    "You are a precise OCR engine. A previous attempt at reading this "
    "cropped region produced:\n\n{previous_text}\n\nThat reading was "
    "REJECTED ({rejection_reason}). Re-transcribe EVERY line of text in "
    "the image carefully; keep line breaks; return only the corrected "
    "text — no commentary. If the crop is blank, return an empty string."
)
```

`ocr_crop(self, input_path, page_index, bbox, *, previous_text: str = "", attempt: int = 1) -> str` builds the prompt from `REPAIR_CROP_PROMPT.format(previous_text=sanitize_prompt_input(previous_text), rejection_reason="confidence below target or garbled")` when `previous_text` else `CROP_OCR_PROMPT`, and calls a new `_call_with_retry(crop_b64, prompt=..., temperature=TEMPERATURE_GROUNDED + 0.1 * (attempt - 1), ...)` capped at 0.3 — thread an optional `temperature` param through `_call_with_retry` (default `TEMPERATURE_GROUNDED`).

`workflows/grounded.py re_ocr` accepts the new kwargs and forwards them.

- [ ] **Step 4: Run** `uv run pytest tests/core/workflows tests/core/ocr tests/core/grounded -q` — PASS.
- [ ] **Step 5: Commit** `-m "ocr: informed repair re-OCR — previous attempt + rejection reason in prompt, retry temperature bump"`

### Task C8: Text-layer agreement as repair trigger (hybrid)

**Files:**
- Modify: `src/omniscribe/core/workflows/hybrid_repair.py` (`_count_repair_targets`, `run_repair_phase` signature), new helper in `src/omniscribe/core/recall/text_layer.py`
- Test: `tests/core/workflows/test_repair.py`

- [ ] **Step 1: Failing tests**

```python
def test_text_layer_agreement_flags_fluent_hallucination() -> None:
    # Block "The committee unanimously approved the budget" is well-formed
    # (est conf 0.99 → normally exempt) but the PDF text layer for the page
    # says "Der Haushalt wurde einstimmig genehmigt" → agreement ~0 →
    # counted as a repair target when text layer provided.

def test_text_layer_agreement_passes_real_text() -> None:
    # Same English sentence present in the layer → agreement high →
    # NOT a target.
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement** in `text_layer.py`:

```python
def token_agreement(ocr_text: str, layer_text: str) -> float:
    """Share of OCR tokens present in the PDF text layer (0..1).

    The repair trigger's text-shape heuristic can't see fluent
    hallucinations (they score 0.99 like real text); a low token overlap
    against the PDF's own text layer is the missing signal.
    """
    if not ocr_text.strip() or not layer_text.strip():
        return 1.0  # no evidence → don't flag
    layer_tokens = {t.casefold() for t in re.findall(r"\w+", layer_text)}
    ocr_tokens = [t.casefold() for t in re.findall(r"\w+", ocr_text) if len(t) >= 3]
    if not ocr_tokens:
        return 1.0
    hits = sum(1 for t in ocr_tokens if t in layer_tokens)
    return hits / len(ocr_tokens)
```

In `hybrid_repair.py`: `run_repair_phase` and `_count_repair_targets` gain `text_layers: dict[int, str] | None = None`; a block is a target when `_estimate_confidence(text) < target` **or** (`text_layers` is not None and `_estimate_confidence(text) >= WELL_FORMED_CONFIDENCE` and `token_agreement(text, text_layers.get(p_num, "")) < 0.2`). Wire the caller in `hybrid.py`: pass the recall booster's text layer text when that stage is enabled (locate where `PdfTextLayerRecall` is instantiated in the engine; expose `layer.page_text(p_num)` — add a thin accessor if absent; if the engine structure makes this awkward, extract the layer text once per run into the dict and pass it).

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "ocr: text-layer agreement flags fluent hallucinations as repair targets"`

### Task C9: GLM parser hardening + drop events

**Files:**
- Modify: `src/omniscribe/core/grounded/parsers.py:209-227` (GLM path), `src/omniscribe/core/ocr_quality/events.py` callers in parsers
- Test: `tests/core/grounded/test_parsers.py` (locate existing GLM parser tests)

- [ ] **Step 1: Failing tests**

```python
def test_glm_parser_bbox_aliases_and_guards() -> None:
    # Block with "bbox" instead of "bbox_2d" still parses.
    # Block missing any bbox key is skipped (no KeyError) and counted.

def test_glm_parser_emits_drop_events(caplog) -> None:
    # A malformed block logs/emits one drop event with the reason.
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

```python
        bbox_raw = b.get("bbox_2d") or b.get("bbox") or b.get("box")
        if (
            not isinstance(bbox_raw, (list, tuple))
            or len(bbox_raw) != 4
        ):
            emit(
                "parsers",
                doc_id="-",
                page=page_index,
                duration_ms=0,
                decision="drop:missing_bbox",
                fallback_used=False,
            )
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in bbox_raw)
        except (TypeError, ValueError):
            emit(..., decision="drop:bad_bbox", ...)
            continue
```

(`emit` import from `omniscribe.core.ocr_quality.events` — never raises, so parser stays safe; keep the existing structural-label deny-list.)

- [ ] **Step 4: Run** — PASS. **Step 5: Commit** `-m "grounded: GLM bbox aliases + guards, structured drop events"`

### Task C10: Correction pass fallback + PROMPT_VERSION split

**Files:**
- Modify: `src/omniscribe/core/ocr/processor.py:388-402` (page) and `:508-518` (crop), `src/omniscribe/core/grounded/prompted.py:57` (`GROUNDED_PROMPT_VERSION`), `src/omniscribe/core/translate/nodes.py:39` (`TRANSLATION_PROMPT_VERSION`)
- Test: `tests/core/ocr/test_ocr_processor.py` (nearest)

- [ ] **Step 1: Failing tests**

```python
async def test_page_correction_empty_keeps_first_pass(...) -> None:
    # First _chat returns good text, correction returns "" → result keeps
    # the first-pass text (not []).

async def test_crop_correction_fallback_keeps_first_pass(...) -> None:
    # Correction returns a fallback response → first-pass text survives.

def test_prompt_versions_are_independent_constants() -> None:
    from omniscribe.core.ocr import prompts as ocr_prompts
    from omniscribe.core.grounded import prompted
    from omniscribe.core.translate import nodes

    assert prompted.GROUNDED_PROMPT_VERSION == ocr_prompts.PROMPT_VERSION
    assert nodes.TRANSLATION_PROMPT_VERSION == ocr_prompts.PROMPT_VERSION
    # Same value today, independent names so future bumps don't collide.
```

- [ ] **Step 2: Run** — FAIL. **Step 3: Implement:**

Page path (replaces `if not text: return []` after correction):

```python
        if self_correction:
            first_pass = text
            correction_prompt = fill_correction_page(text)
            corrected = await self._chat(...)
            # accept-always erased valid first passes when the correction
            # came back empty/fallback (audit finding); fall back instead.
            text = (
                corrected
                if corrected and not _is_fallback_response(corrected)
                else first_pass
            )
```

Crop path: same pattern — on empty/fallback correction, keep the first-pass `text`.

Renames: `grounded/prompted.py` — `PROMPT_VERSION` → `GROUNDED_PROMPT_VERSION` (update `__all__` + any importers; grep first, update tests). `translate/nodes.py` — `PROMPT_VERSION` → `TRANSLATION_PROMPT_VERSION` (update re-exports/tests importing it).

- [ ] **Step 4: Run** full fast-tier — PASS. **Step 5: Commit** `-m "ocr: correction-pass fallback + independent PROMPT_VERSION constants (closes outstanding-work 6.55)"`

---

## Self-review notes

- C2/C3 need live inspection of `plugins/documents/schemas.py` and the export modal — their test skeletons are marked as locate-then-write; the assertions are concrete.
- C7 changes the `ReOcrBlock` contract; grep test doubles implementing `re_ocr(block_idx, bbox)` and update them to accept the kwargs.
- C8 wiring point in `hybrid.py` is the only genuinely open question; if the engine doesn't hold a text-layer instance, extract layer text at `run_repair_phase` call time via `PdfTextLayerRecall.open()` on the input path (the recall stage already gates on file type).
- Circuit breaker verified correct in the audit (keyed per endpoint) — untouched.
