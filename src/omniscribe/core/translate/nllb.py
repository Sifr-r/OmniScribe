"""NLLB-200 fast translation engine.

Wraps :mod:`transformers` to provide CPU-friendly translation when the user
selects "fast" mode or when the configured LLM is unavailable. Lazy-loaded.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import typing
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Map UI-friendly language names to NLLB language codes. Keys are
# lower-cased and stripped before lookup, so the Flutter client's display
# labels ("Chinese (Simplified)") must appear here verbatim. Every label the
# client offers in ``_languages`` MUST have an entry — a missing key used to
# resolve to English and silently mistranslate the whole request.
LANGUAGE_CODE_MAP: dict[str, str] = {
    "english": "eng_Latn",
    "spanish": "spa_Latn",
    "french": "fra_Latn",
    "german": "deu_Latn",
    "arabic": "arb_Arab",
    "chinese": "zho_Hans",
    "chinese (simplified)": "zho_Hans",
    "chinese (traditional)": "zho_Hant",
    "japanese": "jpn_Jpan",
    "korean": "kor_Hang",
    "russian": "rus_Cyrl",
    "portuguese": "por_Latn",
    "italian": "ita_Latn",
    "dutch": "nld_Latn",
    "swedish": "swe_Latn",
}

#: Canonical target labels the NLLB path accepts: every entry the Flutter
#: client offers (``client/lib/features/translation/translation_screen.dart``
#: ``_languages``) plus the API default. Keep in sync with that list — a new
#: client label without a map entry is a validation error, not a fallback.
SUPPORTED_LANGUAGES: tuple[str, ...] = (
    "English",
    "French",
    "Spanish",
    "German",
    "Italian",
    "Portuguese",
    "Japanese",
    "Chinese (Simplified)",
    "Korean",
    "Russian",
    "Arabic",
    "Dutch",
)

#: The NLLB fallback path has no source-language selector (neither the API
#: nor the client sends one), so the source stays pinned to English. Making it
#: a named constant documents that as a deliberate scope decision rather than
#: an accident; adding multilingual source selection is a separate feature.
DEFAULT_SOURCE_LANGUAGE = "eng_Latn"


class UnsupportedLanguageError(ValueError):
    """Raised when a target language cannot be mapped to an NLLB code.

    Subclasses ``ValueError`` so Pydantic field validators surface it as a
    regular 422 validation error at the HTTP edge.
    """

    def __init__(self, language: str) -> None:
        self.language = language
        super().__init__(
            f"Unsupported NLLB target language {language!r}. "
            f"Supported languages: {', '.join(SUPPORTED_LANGUAGES)}"
        )


def resolve_nllb_code(language: str) -> str:
    """Map a UI language label (or NLLB code) to its NLLB-200 language code.

    Unknown labels raise :class:`UnsupportedLanguageError` instead of falling
    back to English — silently translating a Korean request into English is a
    correctness bug, not a graceful default.
    """
    raw = (language or "").strip()
    key = raw.lower()
    if key in LANGUAGE_CODE_MAP:
        return LANGUAGE_CODE_MAP[key]
    if raw in LANGUAGE_CODE_MAP.values():
        return raw
    raise UnsupportedLanguageError(language)


@dataclass(slots=True)
class NLLBResult:
    text: str
    source_lang: str
    target_lang: str


class NLLBEngine:
    """Lazy wrapper around :mod:`transformers`' NLLB-200 pipeline."""

    DEFAULT_MODEL = "facebook/nllb-200-distilled-600M"

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._lock = threading.Lock()
        self._pipeline: typing.Any = None
        self._tokenizer: typing.Any = None

    def is_available(self) -> bool:
        try:
            import transformers  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_loaded(self) -> None:
        if self._pipeline is not None:
            return
        with self._lock:
            if self._pipeline is not None:
                return
            try:
                import torch
                from transformers import (
                    AutoModelForSeq2SeqLM,
                    AutoTokenizer,
                    pipeline,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "NLLBEngine requires the 'nllb' extra (transformers + torch)."
                ) from exc
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            pipe_fn: typing.Any = pipeline
            self._pipeline = pipe_fn(
                "translation",
                model=model,
                tokenizer=self._tokenizer,
                device=0 if device == "cuda" else -1,
            )
            logger.info("NLLBEngine loaded model=%s device=%s", self.model_name, device)

    async def translate(self, text: str, target_language: str) -> NLLBResult:
        """Translate ``text`` to ``target_language``.

        ``target_language`` may be a UI-friendly name (e.g. ``"French"``) or an
        NLLB code (e.g. ``"fra_Latn"``).
        """
        target_code = resolve_nllb_code(target_language)
        if not text or not text.strip():
            return NLLBResult(text="", source_lang="auto", target_lang=target_language)
        await asyncio.to_thread(self._ensure_loaded)
        # ``get_running_loop`` is the coroutine-safe replacement for the
        # deprecated ``get_event_loop`` (audit M-domain 4): it returns
        # the current running loop, with no fallback path that would
        # spin a new loop on a worker thread.
        loop = asyncio.get_running_loop()
        source_code = DEFAULT_SOURCE_LANGUAGE  # see the constant's note

        def _run() -> NLLBResult:
            assert self._pipeline is not None
            out = self._pipeline(
                text,
                src_lang=source_code,
                tgt_lang=target_code,
                max_length=1024,
            )
            translated = ""
            if isinstance(out, list) and out:
                first = out[0]
                if isinstance(first, dict):
                    translated = str(first.get("translation_text", ""))
            return NLLBResult(
                text=translated.strip(),
                source_lang=source_code,
                target_lang=target_code,
            )

        return await loop.run_in_executor(None, _run)
