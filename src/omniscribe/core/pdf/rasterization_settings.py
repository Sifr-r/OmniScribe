"""Settings for the rasterization / embed / VLM-grounded JPEG quality tunables.

These knobs control how :mod:`omniscribe.core.pdf.rasterizer`,
:mod:`omniscribe.core.pdf.embedder`, and
:mod:`omniscribe.core.grounded.rasterize` encode page images and bound
memory usage. They were originally hardcoded module-level constants in
``rasterizer.py``; the deep-refactor audit (§4.7, preserved in Git history)
recommended externalization so operators can tune memory/quality
trade-offs without code edits.

Env vars (all optional; invalid or out-of-range values fall back to the
defaults rather than crashing at import time, matching the
``TranslationSettings`` pattern):

``OMNISCRIBE_RASTERIZER_MAX_SAFE_PIXELS``
    Pixel cap for PyMuPDF page rasterization. Pages whose
    ``width x height x (dpi/72)**2`` would exceed this value get their
    requested DPI clamped down so the rasterized bitmap stays within the
    budget. Larger values increase per-page memory; smaller values force
    coarser output on dense blueprints.

``OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH``
    JPEG quality (1..100) used when rasterizer encodes PDF pages for the
    VLM (Surya detect → VLM OCR pipeline). Lower values shrink the
    base64 payload sent to the model at the cost of fidelity.

``OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED``
    JPEG quality used by the grounded-VLM rasterize path. Kept separate
    from the hybrid path because the grounded prompt is more visually
    sensitive and the team tuned each independently.

``OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_PDF``
    JPEG quality used by the PDF-text-layer embedder when embedding
    recognized text back into a PDF page. Higher values inflate the
    output PDF size.

``OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE``
    JPEG quality used by the embedder for raw-image inputs (where the
    original was a JPEG/PNG/BMP/etc., not a PDF page).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# --- Defaults (match the prior module-level constants) ---------------------

DEFAULT_RASTERIZER_MAX_SAFE_PIXELS = 25_000_000
DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH = 50
DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED = 80
DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF = 80
DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE = 85

# M5 audit fix: 500 MPixels is ~20x the default budget (25 MPixels)
# while rejecting accidental 10x typos that would allocate ~100 GB.
# The previous 10 GPixels ceiling let a typo like ``25000000000``
# slip through.
_MAX_SAFE_PIXELS_CEILING = 500_000_000


@dataclass(frozen=True, slots=True)
class RasterizationSettings:
    """Tunables for the rasterization / embed / VLM-grounded JPEG encoders.

    Defaults match the prior hardcoded module-level constants so behaviour
    is unchanged when no env vars are set. See the module docstring for
    the per-field semantic and the corresponding env-var name.
    """

    max_safe_pixels: int = DEFAULT_RASTERIZER_MAX_SAFE_PIXELS
    vlm_jpeg_quality_pdf_path: int = DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    vlm_jpeg_quality_grounded: int = DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED
    embed_jpeg_quality_pdf: int = DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF
    embed_jpeg_quality_image: int = DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE

    def __post_init__(self) -> None:
        # max_safe_pixels: integer, 1 <= value <= _MAX_SAFE_PIXELS_CEILING.
        _validate_int_type("max_safe_pixels", self.max_safe_pixels)
        if self.max_safe_pixels < 1 or self.max_safe_pixels > _MAX_SAFE_PIXELS_CEILING:
            raise ValueError(
                f"max_safe_pixels must be between 1 and {_MAX_SAFE_PIXELS_CEILING} "
                f"(got {self.max_safe_pixels}); check the env value"
            )

        # JPEG quality fields: integer 1..100.
        for field_name in (
            "vlm_jpeg_quality_pdf_path",
            "vlm_jpeg_quality_grounded",
            "embed_jpeg_quality_pdf",
            "embed_jpeg_quality_image",
        ):
            value = getattr(self, field_name)
            _validate_int_type(field_name, value)
            if not 1 <= value <= 100:
                raise ValueError(
                    f"{field_name} must be between 1 and 100 (JPEG quality); "
                    f"got {value}"
                )

    @classmethod
    def from_env(cls) -> RasterizationSettings:
        """Build settings from environment variables.

        Invalid values (non-numeric, out of range, or empty) fall back to
        the per-field defaults rather than raising — env misconfig should
        not crash the server at import time. Same pattern as
        :meth:`omniscribe.core.translate.config.TranslationSettings.from_env`.
        """
        return cls(
            max_safe_pixels=_int_env(
                "OMNISCRIBE_RASTERIZER_MAX_SAFE_PIXELS",
                DEFAULT_RASTERIZER_MAX_SAFE_PIXELS,
                minimum=1,
                maximum=_MAX_SAFE_PIXELS_CEILING,
            ),
            vlm_jpeg_quality_pdf_path=_int_env(
                "OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH",
                DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH,
                minimum=1,
                maximum=100,
            ),
            vlm_jpeg_quality_grounded=_int_env(
                "OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED",
                DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED,
                minimum=1,
                maximum=100,
            ),
            embed_jpeg_quality_pdf=_int_env(
                "OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_PDF",
                DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF,
                minimum=1,
                maximum=100,
            ),
            embed_jpeg_quality_image=_int_env(
                "OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE",
                DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE,
                minimum=1,
                maximum=100,
            ),
        )

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> RasterizationSettings:
        """Build settings from a runtime config mapping (raises on bad types)."""
        return cls(
            max_safe_pixels=_int_value(
                values,
                "max_safe_pixels",
                DEFAULT_RASTERIZER_MAX_SAFE_PIXELS,
                minimum=1,
                maximum=_MAX_SAFE_PIXELS_CEILING,
            ),
            vlm_jpeg_quality_pdf_path=_int_value(
                values,
                "vlm_jpeg_quality_pdf_path",
                DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH,
                minimum=1,
                maximum=100,
            ),
            vlm_jpeg_quality_grounded=_int_value(
                values,
                "vlm_jpeg_quality_grounded",
                DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED,
                minimum=1,
                maximum=100,
            ),
            embed_jpeg_quality_pdf=_int_value(
                values,
                "embed_jpeg_quality_pdf",
                DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF,
                minimum=1,
                maximum=100,
            ),
            embed_jpeg_quality_image=_int_value(
                values,
                "embed_jpeg_quality_image",
                DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE,
                minimum=1,
                maximum=100,
            ),
        )


def _validate_int_type(field_name: str, value: object) -> None:
    """Reject bool (subclass of int) and non-int types.

    Range validation is handled separately by callers (``__post_init__`` raises
    on out-of-range; :func:`_int_value` / :func:`_int_env` fall back to the
    default). Keeping type and range checks separate lets the
    runtime-config mapping path match the env-var path: bad types raise,
    out-of-range values silently fall back.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")


def _int_env(name: str, default: int, *, minimum: int, maximum: int | None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed


def _int_value(
    values: Mapping[str, object],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None,
) -> int:
    value: object = values.get(key, default)
    _validate_int_type(key, value)
    # ``_validate_int_type`` has ruled out bool and non-int; the rest of the
    # narrowing is for mypy's benefit so the comparison ops have a numeric
    # operand type.
    assert isinstance(value, int)
    if value < minimum:
        return default
    if maximum is not None and value > maximum:
        return default
    return value
