from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_TRANSLATION_API_BASE = "http://localhost:1234/v1"
DEFAULT_TRANSLATION_API_KEY = "lm-studio"
DEFAULT_TRANSLATION_MODEL = "allenai/olmocr-2-7b"
DEFAULT_TRANSLATION_MAX_ATTEMPTS = 3
DEFAULT_TRANSLATION_MIN_LENGTH_RATIO = 0.1
DEFAULT_TRANSLATION_MAX_LENGTH_RATIO = 2.5
DEFAULT_TRANSLATION_ACCEPTANCE_SCORE = 0.8
DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT = 3
DEFAULT_TRANSLATION_LEXICON_MIN_SCORE = 0.35
DEFAULT_TRANSLATION_EVALUATE = True
DEFAULT_TRANSLATION_MAX_TOKENS = 2048
DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP = 20


class AsyncTranslationUnavailable(RuntimeError):
    """Raised when the optional async translation runtime is unavailable."""


class TranslationError(RuntimeError):
    """Raised when every translation attempt failed (no usable output)."""


@dataclass(frozen=True, slots=True)
class TranslationSettings:
    """OpenAI-compatible endpoint settings used by async translation.

    Tunables for the translate/evaluate loop (``max_attempts``,
    ``min_length_ratio``, ``acceptance_score``) default to the package-level
    constants but can be overridden via env vars or
    :meth:`from_mapping` to make tuning the loop possible without code edits.
    """

    api_base: str = DEFAULT_TRANSLATION_API_BASE
    api_key: str = DEFAULT_TRANSLATION_API_KEY
    model: str = DEFAULT_TRANSLATION_MODEL
    max_attempts: int = DEFAULT_TRANSLATION_MAX_ATTEMPTS
    min_length_ratio: float = DEFAULT_TRANSLATION_MIN_LENGTH_RATIO
    max_length_ratio: float = DEFAULT_TRANSLATION_MAX_LENGTH_RATIO
    acceptance_score: float = DEFAULT_TRANSLATION_ACCEPTANCE_SCORE
    lexicon_result_count: int = DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT
    lexicon_min_score: float = DEFAULT_TRANSLATION_LEXICON_MIN_SCORE
    evaluate_enabled: bool = DEFAULT_TRANSLATION_EVALUATE
    max_tokens: int = DEFAULT_TRANSLATION_MAX_TOKENS
    entity_memory_cap: int = DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP

    def __post_init__(self) -> None:
        for field_name, value in (
            ("api_base", self.api_base),
            ("api_key", self.api_key),
            ("model", self.model),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")

        if isinstance(self.max_attempts, bool) or not isinstance(
            self.max_attempts, int
        ):
            raise ValueError("max_attempts must be an integer")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        if isinstance(self.min_length_ratio, bool) or not isinstance(
            self.min_length_ratio, (int, float)
        ):
            raise ValueError("min_length_ratio must be a number")
        if not 0.0 <= float(self.min_length_ratio) <= 1.0:
            raise ValueError("min_length_ratio must be between 0.0 and 1.0")

        if isinstance(self.max_length_ratio, bool) or not isinstance(
            self.max_length_ratio, (int, float)
        ):
            raise ValueError("max_length_ratio must be a number")
        if float(self.max_length_ratio) < 1.0:
            raise ValueError("max_length_ratio must be >= 1.0")

        if isinstance(self.acceptance_score, bool) or not isinstance(
            self.acceptance_score, (int, float)
        ):
            raise ValueError("acceptance_score must be a number")
        if not 0.0 <= float(self.acceptance_score) <= 1.0:
            raise ValueError("acceptance_score must be between 0.0 and 1.0")

        for int_field in ("lexicon_result_count", "max_tokens", "entity_memory_cap"):
            value = getattr(self, int_field)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{int_field} must be an integer")
            if value < 1:
                raise ValueError(f"{int_field} must be >= 1")

        if isinstance(self.lexicon_min_score, bool) or not isinstance(
            self.lexicon_min_score, (int, float)
        ):
            raise ValueError("lexicon_min_score must be a number")
        if not 0.0 <= float(self.lexicon_min_score) <= 1.0:
            raise ValueError("lexicon_min_score must be between 0.0 and 1.0")

    @classmethod
    def from_env(cls) -> TranslationSettings:
        """Build settings from environment variables.

        Endpoint fields reuse the shared ``LLM_*`` vars; the per-loop tunables
        use ``OMNISCRIBE_TRANSLATION_*`` names so they can be tuned without
        code changes. Invalid values for the tunables fall back to the
        defaults rather than raising — env misconfig should not crash the
        server at import time.
        """
        return cls(
            api_base=os.getenv("LLM_API_BASE", DEFAULT_TRANSLATION_API_BASE),
            api_key=os.getenv("LLM_API_KEY", DEFAULT_TRANSLATION_API_KEY),
            model=os.getenv("LLM_MODEL", DEFAULT_TRANSLATION_MODEL),
            max_attempts=_int_env(
                "OMNISCRIBE_TRANSLATION_MAX_ATTEMPTS",
                DEFAULT_TRANSLATION_MAX_ATTEMPTS,
                minimum=1,
            ),
            min_length_ratio=_float_env(
                "OMNISCRIBE_TRANSLATION_MIN_LENGTH_RATIO",
                DEFAULT_TRANSLATION_MIN_LENGTH_RATIO,
                minimum=0.0,
                maximum=1.0,
            ),
            max_length_ratio=_float_env(
                "OMNISCRIBE_TRANSLATION_MAX_LENGTH_RATIO",
                DEFAULT_TRANSLATION_MAX_LENGTH_RATIO,
                minimum=1.0,
                maximum=None,
            ),
            acceptance_score=_float_env(
                "OMNISCRIBE_TRANSLATION_ACCEPTANCE_SCORE",
                DEFAULT_TRANSLATION_ACCEPTANCE_SCORE,
                minimum=0.0,
                maximum=1.0,
            ),
            lexicon_result_count=_int_env(
                "OMNISCRIBE_TRANSLATION_LEXICON_RESULT_COUNT",
                DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT,
                minimum=1,
            ),
            lexicon_min_score=_float_env(
                "OMNISCRIBE_TRANSLATION_LEXICON_MIN_SCORE",
                DEFAULT_TRANSLATION_LEXICON_MIN_SCORE,
                minimum=0.0,
                maximum=1.0,
            ),
            evaluate_enabled=_bool_env(
                "OMNISCRIBE_TRANSLATION_EVALUATE", DEFAULT_TRANSLATION_EVALUATE
            ),
            max_tokens=_int_env(
                "OMNISCRIBE_TRANSLATION_MAX_TOKENS",
                DEFAULT_TRANSLATION_MAX_TOKENS,
                minimum=1,
            ),
            entity_memory_cap=_int_env(
                "OMNISCRIBE_TRANSLATION_ENTITY_MEMORY_CAP",
                DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP,
                minimum=1,
            ),
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> TranslationSettings:
        """Build settings from a broader runtime config mapping."""
        return cls(
            api_base=_string_value(values, "api_base", DEFAULT_TRANSLATION_API_BASE),
            api_key=_string_value(values, "api_key", DEFAULT_TRANSLATION_API_KEY),
            model=_string_value(values, "model", DEFAULT_TRANSLATION_MODEL),
            max_attempts=_int_value(
                values, "max_attempts", DEFAULT_TRANSLATION_MAX_ATTEMPTS, minimum=1
            ),
            min_length_ratio=_numeric_value(
                values,
                "min_length_ratio",
                DEFAULT_TRANSLATION_MIN_LENGTH_RATIO,
                minimum=0.0,
                maximum=1.0,
            ),
            max_length_ratio=_numeric_value(
                values,
                "max_length_ratio",
                DEFAULT_TRANSLATION_MAX_LENGTH_RATIO,
                minimum=1.0,
                maximum=None,
            ),
            acceptance_score=_numeric_value(
                values,
                "acceptance_score",
                DEFAULT_TRANSLATION_ACCEPTANCE_SCORE,
                minimum=0.0,
                maximum=1.0,
            ),
            lexicon_result_count=_int_value(
                values,
                "lexicon_result_count",
                DEFAULT_TRANSLATION_LEXICON_RESULT_COUNT,
                minimum=1,
            ),
            lexicon_min_score=_numeric_value(
                values,
                "lexicon_min_score",
                DEFAULT_TRANSLATION_LEXICON_MIN_SCORE,
                minimum=0.0,
                maximum=1.0,
            ),
            evaluate_enabled=_bool_value(
                values, "evaluate_enabled", DEFAULT_TRANSLATION_EVALUATE
            ),
            max_tokens=_int_value(
                values, "max_tokens", DEFAULT_TRANSLATION_MAX_TOKENS, minimum=1
            ),
            entity_memory_cap=_int_value(
                values,
                "entity_memory_cap",
                DEFAULT_TRANSLATION_ENTITY_MEMORY_CAP,
                minimum=1,
            ),
        )


def _string_value(values: Mapping[str, object], key: str, default: str) -> str:
    value = values.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Env %s=%r is not a valid integer; using default %s", name, raw, default
        )
        return default
    if parsed < minimum:
        logger.warning(
            "Env %s=%r is below the minimum %s; using default %s",
            name,
            raw,
            minimum,
            default,
        )
        return default
    return parsed


def _float_env(
    name: str, default: float, *, minimum: float, maximum: float | None
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = float(raw)
    except ValueError:
        logger.warning(
            "Env %s=%r is not a valid number; using default %s", name, raw, default
        )
        return default
    if parsed < minimum:
        logger.warning(
            "Env %s=%r is below the minimum %s; using default %s",
            name,
            raw,
            minimum,
            default,
        )
        return default
    if maximum is not None and parsed > maximum:
        logger.warning(
            "Env %s=%r is above the maximum %s; using default %s",
            name,
            raw,
            maximum,
            default,
        )
        return default
    return parsed


_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _BOOL_TRUE:
        return True
    if value in _BOOL_FALSE:
        return False
    logger.warning(
        "Env %s=%r is not a valid boolean; using default %s", name, raw, default
    )
    return default


def _bool_value(values: Mapping[str, object], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped in _BOOL_TRUE:
            return True
        if stripped in _BOOL_FALSE:
            return False
    raise ValueError(f"{key} must be a boolean")


def _int_value(
    values: Mapping[str, object], key: str, default: int, *, minimum: int
) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value if value >= minimum else default


def _numeric_value(
    values: Mapping[str, object],
    key: str,
    default: float,
    *,
    minimum: float,
    maximum: float | None,
) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    fvalue = float(value)
    if fvalue < minimum:
        return default
    if maximum is not None and fvalue > maximum:
        return default
    return fvalue
