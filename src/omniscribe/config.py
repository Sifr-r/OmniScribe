"""Central runtime configuration for OmniScribe.

All application-owned environment variables are declared in one Pydantic
settings model. Modules consume :func:`load_settings` rather than reading
``os.environ`` directly, which gives startup validation a single boundary and
keeps local/test overrides deterministic.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_GROUNDED_MODEL: str = "qwen/qwen3-vl-8b"


class _DecodeLenientComplexMixin:
    """Allows CSV or non-JSON strings from environment sources for complex list fields."""

    def decode_complex_value(self, field_name: str, field_info: Any, value: Any) -> Any:
        if field_name == "cors_origins":
            try:
                return json.loads(value)
            except (json.JSONDecodeError, ValueError, TypeError):
                return value
        return super().decode_complex_value(field_name, field_info, value)  # type: ignore[misc]


class _CustomEnvSettingsSource(_DecodeLenientComplexMixin, EnvSettingsSource):
    pass


class _CustomDotEnvSettingsSource(_DecodeLenientComplexMixin, DotEnvSettingsSource):
    pass


class RuntimeSettings(BaseSettings):
    """Environment-backed settings shared by the API and core pipeline."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        env_ignore_empty=True,
        populate_by_name=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _CustomEnvSettingsSource(settings_cls),
            _CustomDotEnvSettingsSource(settings_cls),
            file_secret_settings,
        )

    llm_api_base: str = Field(
        default="http://localhost:1234/v1",
        validation_alias=AliasChoices("LLM_API_BASE", "OMNISCRIBE_LLM_API_BASE"),
    )
    llm_api_key: str = Field(
        default="lm-studio",
        validation_alias=AliasChoices("LLM_API_KEY", "OMNISCRIBE_LLM_API_KEY"),
    )
    llm_model: str = Field(
        default="allenai/olmocr-2-7b",
        validation_alias=AliasChoices("LLM_MODEL", "OMNISCRIBE_LLM_MODEL"),
    )
    # ``LLM_MODEL`` is shared across the hybrid and grounded engines when no
    # engine-specific override is provided. ``OMNISCRIBE_GROUNDED_MODEL``
    # remains the only dedicated alias for the grounded engine; the shared
    # default is applied in :meth:`_default_grounded_model`.
    grounded_model: str = Field(
        default=DEFAULT_GROUNDED_MODEL,
        validation_alias="OMNISCRIBE_GROUNDED_MODEL",
    )

    vlm_page_timeout: float = Field(
        default=240.0, validation_alias="OMNISCRIBE_VLM_PAGE_TIMEOUT", gt=0
    )
    vlm_crop_timeout: float = Field(
        default=60.0, validation_alias="OMNISCRIBE_VLM_CROP_TIMEOUT", gt=0
    )
    llm_max_retries: int = Field(
        default=2, validation_alias="OMNISCRIBE_LLM_MAX_RETRIES", ge=0
    )
    llm_retry_base_delay: float = Field(
        default=1.0, validation_alias="OMNISCRIBE_LLM_RETRY_BASE_DELAY", ge=0
    )
    cb_failure_threshold: int = Field(
        default=5, validation_alias="OMNISCRIBE_CB_FAILURE_THRESHOLD", ge=1
    )
    cb_cooldown: float = Field(
        default=30.0, validation_alias="OMNISCRIBE_CB_COOLDOWN", ge=0
    )

    artifact_base_dir: Path = Field(
        default_factory=lambda: Path(tempfile.gettempdir()),
        validation_alias="OMNISCRIBE_ARTIFACT_DIR",
    )

    # OCR quality repair-loop seeds (pedantic 7.12). The shipped
    # ``cordis.yml`` also reads these env vars directly via
    # ``${OMNISCRIBE_QUALITY_*:-default}`` expansion at plugin boot —
    # declaring them here makes them first-class fields of
    # :class:`RuntimeSettings` so anything that consumes
    # :func:`load_settings` (settings dump, tests, ops tooling) sees
    # the same contract as the boot-time plugin tree. The two paths
    # agree by default; a plugin-row ``config:`` override still wins at
    # boot.
    ocr_quality_loop_enabled: bool = Field(
        default=True,
        validation_alias="OMNISCRIBE_QUALITY_LOOP",
    )
    ocr_quality_target: float = Field(
        default=0.85,
        validation_alias="OMNISCRIBE_QUALITY_TARGET",
        ge=0.5,
        le=1.0,
    )
    ocr_quality_max_retries: int = Field(
        default=2,
        validation_alias="OMNISCRIBE_QUALITY_MAX_RETRIES",
        ge=0,
        le=5,
    )

    # Hard cap on pages a single run is allowed to rasterize (pedantic
    # 3.3). Declared here for the env-var inventory and ops tooling that
    # consumes :func:`load_settings`; the runtime read still happens via
    # ``os.getenv`` in :mod:`omniscribe.core.pdf.rasterizer` so the
    # per-page cap is hot-reloadable from a long-running uvicorn
    # worker without a process restart. Both paths default to 500 and
    # agree on the contract (``0`` or unparseable → cap disabled).
    max_pages: int = Field(
        default=500,
        validation_alias="OMNISCRIBE_MAX_PAGES",
        ge=0,
    )
    chunk_pages: int = Field(
        default=25, validation_alias="OMNISCRIBE_CHUNK_PAGES", ge=1
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0", validation_alias="REDIS_URL"
    )

    # State backend selector (audit A-3, 4.24; Phase 2.3 default flip).
    # ``sqlite`` is the default since 2026-09-05: it persists job records,
    # artifact metadata, and progress-channel state across restarts in a
    # single file at ``<artifact_dir>/omniscribe-state.db`` (WAL mode).
    # ``memory`` keeps every store in the local process; setting it
    # explicitly triggers a ``WARN`` log at boot. ``redis`` is deferred
    # and not yet implemented in the harness. The selector is validated
    # at startup; unsupported backends fail fast.
    state_backend: str = Field(
        default="sqlite",
        validation_alias="OMNISCRIBE_STATE_BACKEND",
    )
    # Cordis-style harness boot config. ``cordis_config_path`` is the base
    # plugin tree (the package ships one under ``resources/cordis.yml``);
    # patch files are layered on top by :meth:`cordis_patch_paths`.
    cordis_config_path: Path = Field(
        default_factory=lambda: Path(__file__).parent / "resources" / "cordis.yml",
        validation_alias="OMNISCRIBE_CORDIS_CONFIG",
    )
    cordis_patch_paths_raw: str | None = Field(
        default=None,
        validation_alias="OMNISCRIBE_CORDIS_PATCH",
    )

    allow_ssrf_local: bool = Field(default=False, validation_alias="ALLOW_SSRF_LOCAL")
    log_level: str = Field(default="INFO", validation_alias="OMNISCRIBE_LOG_LEVEL")
    log_format: str = Field(default="json", validation_alias="OMNISCRIBE_LOG_FORMAT")

    auth_token: str | None = Field(
        default=None,
        validation_alias="OMNISCRIBE_AUTH_TOKEN",
    )
    # Transcription auth token: currently consumed only by the
    # transcription config store as a mask source (so the
    # ``/api/config/transcription`` response can preview the token
    # without exposing it in the clear). The auth middleware that will
    # actually enforce it is deferred — see
    # ``docs/outstanding-work.md`` §5 "Deferred capabilities".
    transcription_auth_token: str | None = Field(
        default=None,
        validation_alias="OMNISCRIBE_TRANSCRIPTION_AUTH_TOKEN",
    )
    cors_origins: list[str] = Field(
        default_factory=list,
        validation_alias="OMNISCRIBE_CORS_ORIGINS",
    )
    max_upload_mb: int = Field(
        # Phase 3.1 (2026-09-05): default lowered from 10 GB to 1 GB.
        # The old 10 GB let a LAN caller (with bearer auth) pin 10 GB
        # of memory and disk per request; the cap was enforced, just
        # generous. The 1 GB default matches the DEPLOYMENT.md LAN
        # recipe (which sets 2 GB); operators who genuinely process
        # 5-10 GB batches can raise it explicitly via env var or
        # compose override.
        default=1_024,
        validation_alias="OMNISCRIBE_MAX_UPLOAD_MB",
    )
    rate_limit_per_min: int | None = Field(
        default=None,
        validation_alias="OMNISCRIBE_RATE_LIMIT_PER_MIN",
    )

    @field_validator("max_upload_mb", mode="before")
    @classmethod
    def _normalize_max_upload_mb(cls, value: object) -> int:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 1_024
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Ignoring invalid integer environment value for %s",
                "OMNISCRIBE_MAX_UPLOAD_MB",
            )
            return 1_024

    @field_validator("rate_limit_per_min", mode="before")
    @classmethod
    def _normalize_rate_limit(cls, value: object) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Ignoring invalid integer environment value for %s",
                "OMNISCRIBE_RATE_LIMIT_PER_MIN",
            )
            return None

    @field_validator("state_backend", mode="before")
    @classmethod
    def _normalize_state_backend(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return "memory"
        normalized = str(value).strip().lower()
        if normalized not in {"memory", "sqlite"}:
            raise ValueError(
                f"state backend '{normalized}' is not yet implemented in the plugin harness; "
                "supported backends are 'memory' and 'sqlite'"
            )
        return normalized

    @field_validator("chunk_pages", mode="before")
    @classmethod
    def _normalize_chunk_pages(cls, value: object) -> int:
        if value is None or (isinstance(value, str) and not value.strip()):
            return 25
        try:
            return max(1, int(str(value).strip()))
        except (TypeError, ValueError):
            return 25

    @field_validator(
        "llm_api_base", "llm_api_key", "llm_model", "grounded_model", mode="after"
    )
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must be a non-empty string")
        return value

    @model_validator(mode="after")
    def _inherit_llm_model_for_grounded(self) -> RuntimeSettings:
        """Propagate a shared ``LLM_MODEL`` to the grounded engine.

        ``OMNISCRIBE_GROUNDED_MODEL`` (or an explicit ``grounded_model``
        kwarg) takes precedence; if it is absent but ``LLM_MODEL`` is set in
        the process environment, the grounded model inherits the shared
        value. This keeps the historical convenience for users configuring a
        single VLM without explicitly duplicating it for the grounded engine.
        """
        if (
            os.environ.get("OMNISCRIBE_GROUNDED_MODEL")
            or "grounded_model" in self.model_fields_set
        ):
            return self
        if self.llm_model and self.llm_model == self.grounded_model:
            return self
        # Only inherit when grounded_model is still at its default value,
        # i.e. the user did not provide an explicit grounded override.
        if self.grounded_model == DEFAULT_GROUNDED_MODEL and os.environ.get(
            "LLM_MODEL"
        ):
            object.__setattr__(self, "grounded_model", self.llm_model)
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _normalize_cors_origins(cls, value: object) -> list[str]:
        """Normalize CSV string, iterable, or None into a list of trimmed strings."""
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, (list, tuple, set)):
            result: list[str] = []
            for item in value:
                if item is None:
                    continue
                s = str(item).strip()
                if s:
                    result.append(s)
            return result
        return []

    @field_validator(
        "auth_token",
        "transcription_auth_token",
        mode="after",
    )
    @classmethod
    def _trim_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("rate_limit_per_min", mode="after")
    @classmethod
    def _normalize_non_positive_rate_limit(cls, value: int | None) -> int | None:
        """Normalize non-positive rate limit values to None.

        Values <= 0 (e.g. 0 or negative numbers) disable rate limiting,
        represented as None in runtime configuration.
        """
        return None if value is not None and value <= 0 else value

    @property
    def artifact_directory(self) -> Path:
        """Return the directory used by token-bound artifact stores."""
        return self.artifact_base_dir / "omniscribe"

    @property
    def cors_origins_raw(self) -> str | None:
        """Return raw CSV representation of cors_origins or None if empty."""
        return ",".join(self.cors_origins) if self.cors_origins else None

    @property
    def cordis_patch_paths(self) -> tuple[Path, ...]:
        """Return the cordis.yml patch files layered onto the base tree.

        ``OMNISCRIBE_CORDIS_PATCH`` supplies an explicit comma-separated
        list; otherwise the operator-local default is
        ``<OMNISCRIBE_ARTIFACT_DIR>/cordis.patch.yml``. Entries that do not
        exist on disk are dropped so the loader only sees real files.
        """
        if self.cordis_patch_paths_raw:
            candidates = [
                Path(item.strip()).expanduser()
                for item in self.cordis_patch_paths_raw.split(",")
                if item.strip()
            ]
        else:
            candidates = [self.artifact_base_dir / "cordis.patch.yml"]
        return tuple(path for path in candidates if path.is_file())


def load_settings(**overrides: Any) -> RuntimeSettings:
    """Load validated runtime settings, optionally overriding fields in tests."""
    return RuntimeSettings(**overrides)


__all__ = ["DEFAULT_GROUNDED_MODEL", "RuntimeSettings", "load_settings"]
