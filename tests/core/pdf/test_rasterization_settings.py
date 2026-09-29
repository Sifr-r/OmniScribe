"""Tests for the rasterization / embed / VLM-grounded JPEG-quality tunables.

Covers :mod:`omniscribe.core.pdf.rasterization_settings` plus the
module-level constants it populates in :mod:`omniscribe.core.pdf.rasterizer`.

See the deep-refactor audit (§4.7, preserved in Git history) for the originating finding.
"""

from __future__ import annotations

import pytest

from omniscribe.core.pdf.rasterization_settings import (
    _MAX_SAFE_PIXELS_CEILING,
    DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE,
    DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF,
    DEFAULT_RASTERIZER_MAX_SAFE_PIXELS,
    DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED,
    DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH,
    RasterizationSettings,
)
from omniscribe.core.pdf.rasterizer import (
    EMBED_JPEG_QUALITY_IMAGE,
    EMBED_JPEG_QUALITY_PDF,
    MAX_SAFE_PIXELS,
    VLM_JPEG_QUALITY_GROUNDED,
    VLM_JPEG_QUALITY_PDF_PATH,
)

_RASTERIZER_ENV_NAMES = (
    "OMNISCRIBE_RASTERIZER_MAX_SAFE_PIXELS",
    "OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH",
    "OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED",
    "OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_PDF",
    "OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE",
)


@pytest.fixture
def clean_rasterizer_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _RASTERIZER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_rasterization_settings_defaults() -> None:
    settings = RasterizationSettings()
    assert settings.max_safe_pixels == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS
    assert (
        settings.vlm_jpeg_quality_pdf_path
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    )
    assert (
        settings.vlm_jpeg_quality_grounded
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED
    )
    assert settings.embed_jpeg_quality_pdf == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF
    assert (
        settings.embed_jpeg_quality_image == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE
    )


def test_rasterization_settings_from_env_defaults(
    clean_rasterizer_env: None,
) -> None:
    settings = RasterizationSettings.from_env()
    assert settings.max_safe_pixels == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS
    assert (
        settings.vlm_jpeg_quality_pdf_path
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    )
    assert (
        settings.vlm_jpeg_quality_grounded
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED
    )
    assert settings.embed_jpeg_quality_pdf == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF
    assert (
        settings.embed_jpeg_quality_image == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE
    )


def test_rasterization_settings_from_env_custom(
    clean_rasterizer_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_MAX_SAFE_PIXELS", "50000000")
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH", "60")
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED", "70")
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_PDF", "75")
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE", "90")

    settings = RasterizationSettings.from_env()
    assert settings.max_safe_pixels == 50_000_000
    assert settings.vlm_jpeg_quality_pdf_path == 60
    assert settings.vlm_jpeg_quality_grounded == 70
    assert settings.embed_jpeg_quality_pdf == 75
    assert settings.embed_jpeg_quality_image == 90


def test_rasterization_settings_from_env_invalid_falls_back(
    clean_rasterizer_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Invalid env values must not crash at import time; we fall back to defaults.
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_MAX_SAFE_PIXELS", "not-a-number")
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH", "abc")
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED", "")  # empty
    monkeypatch.setenv("OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_PDF", "0")  # below min
    monkeypatch.setenv(
        "OMNISCRIBE_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE", "200"
    )  # above max

    settings = RasterizationSettings.from_env()
    assert settings.max_safe_pixels == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS
    assert (
        settings.vlm_jpeg_quality_pdf_path
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    )
    assert (
        settings.vlm_jpeg_quality_grounded
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED
    )
    assert settings.embed_jpeg_quality_pdf == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF
    assert (
        settings.embed_jpeg_quality_image == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE
    )


def test_rasterization_settings_from_env_pixel_cap_falls_back(
    clean_rasterizer_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Above the absolute ceiling (10 GPixels) -> fall back to default.
    monkeypatch.setenv(
        "OMNISCRIBE_RASTERIZER_MAX_SAFE_PIXELS", str(_MAX_SAFE_PIXELS_CEILING + 1)
    )
    settings = RasterizationSettings.from_env()
    assert settings.max_safe_pixels == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS


def test_rasterization_settings_from_mapping_defaults() -> None:
    settings = RasterizationSettings.from_mapping({})
    assert settings.max_safe_pixels == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS
    assert (
        settings.vlm_jpeg_quality_pdf_path
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    )
    assert (
        settings.vlm_jpeg_quality_grounded
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED
    )
    assert settings.embed_jpeg_quality_pdf == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF
    assert (
        settings.embed_jpeg_quality_image == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE
    )


def test_rasterization_settings_from_mapping_custom() -> None:
    settings = RasterizationSettings.from_mapping(
        {
            "max_safe_pixels": 30_000_000,
            "vlm_jpeg_quality_pdf_path": 65,
            "vlm_jpeg_quality_grounded": 75,
            "embed_jpeg_quality_pdf": 85,
            "embed_jpeg_quality_image": 95,
        }
    )
    assert settings.max_safe_pixels == 30_000_000
    assert settings.vlm_jpeg_quality_pdf_path == 65
    assert settings.vlm_jpeg_quality_grounded == 75
    assert settings.embed_jpeg_quality_pdf == 85
    assert settings.embed_jpeg_quality_image == 95


def test_rasterization_settings_post_init_validation() -> None:
    # Non-integer / bool input is rejected for every field.
    with pytest.raises(ValueError, match="max_safe_pixels must be an integer"):
        RasterizationSettings(max_safe_pixels="50000000")  # type: ignore

    with pytest.raises(ValueError, match="max_safe_pixels must be an integer"):
        RasterizationSettings(max_safe_pixels=True)

    with pytest.raises(
        ValueError,
        match=r"max_safe_pixels must be between 1 and",
    ):
        RasterizationSettings(max_safe_pixels=0)

    with pytest.raises(
        ValueError,
        match=r"max_safe_pixels must be between 1 and",
    ):
        RasterizationSettings(max_safe_pixels=_MAX_SAFE_PIXELS_CEILING + 1)

    # JPEG quality fields: integer 1..100.
    with pytest.raises(
        ValueError, match="vlm_jpeg_quality_pdf_path must be an integer"
    ):
        RasterizationSettings(vlm_jpeg_quality_pdf_path="60")  # type: ignore

    with pytest.raises(
        ValueError, match="vlm_jpeg_quality_pdf_path must be an integer"
    ):
        RasterizationSettings(vlm_jpeg_quality_pdf_path=True)

    with pytest.raises(
        ValueError, match=r"vlm_jpeg_quality_pdf_path must be between 1 and 100"
    ):
        RasterizationSettings(vlm_jpeg_quality_pdf_path=0)

    with pytest.raises(
        ValueError, match=r"vlm_jpeg_quality_pdf_path must be between 1 and 100"
    ):
        RasterizationSettings(vlm_jpeg_quality_pdf_path=101)

    with pytest.raises(
        ValueError, match=r"vlm_jpeg_quality_grounded must be between 1 and 100"
    ):
        RasterizationSettings(vlm_jpeg_quality_grounded=200)

    with pytest.raises(
        ValueError, match=r"embed_jpeg_quality_pdf must be between 1 and 100"
    ):
        RasterizationSettings(embed_jpeg_quality_pdf=0)

    with pytest.raises(
        ValueError, match=r"embed_jpeg_quality_image must be between 1 and 100"
    ):
        RasterizationSettings(embed_jpeg_quality_image=-5)


def test_rasterization_settings_from_mapping_validation() -> None:
    # Type validation in from_mapping raises (unlike from_env which falls back).
    with pytest.raises(ValueError, match="max_safe_pixels must be an integer"):
        RasterizationSettings.from_mapping({"max_safe_pixels": "50000000"})

    with pytest.raises(
        ValueError, match="vlm_jpeg_quality_pdf_path must be an integer"
    ):
        RasterizationSettings.from_mapping({"vlm_jpeg_quality_pdf_path": True})

    with pytest.raises(ValueError, match="embed_jpeg_quality_image must be an integer"):
        RasterizationSettings.from_mapping({"embed_jpeg_quality_image": None})

    # Out-of-range values fall back to defaults (from_mapping behavior matches
    # from_env: bad config shouldn't crash runtime config calls). Below the
    # floor of 1 and above the per-field ceiling both fall back.
    settings = RasterizationSettings.from_mapping({"max_safe_pixels": 0})
    assert settings.max_safe_pixels == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS

    settings = RasterizationSettings.from_mapping({"vlm_jpeg_quality_pdf_path": -1})
    assert (
        settings.vlm_jpeg_quality_pdf_path
        == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    )

    settings = RasterizationSettings.from_mapping({"embed_jpeg_quality_image": 999})
    assert (
        settings.embed_jpeg_quality_image == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE
    )


def test_rasterizer_module_constants_resolve_to_settings() -> None:
    """The module-level names in rasterizer.py must equal the settings defaults.

    ``embedder.py`` and ``grounded/rasterize.py`` import these names; if they
    ever drift from the settings defaults, downstream tests will catch it.
    """
    assert isinstance(MAX_SAFE_PIXELS, int)
    assert isinstance(VLM_JPEG_QUALITY_PDF_PATH, int)
    assert isinstance(VLM_JPEG_QUALITY_GROUNDED, int)
    assert isinstance(EMBED_JPEG_QUALITY_PDF, int)
    assert isinstance(EMBED_JPEG_QUALITY_IMAGE, int)

    assert MAX_SAFE_PIXELS == DEFAULT_RASTERIZER_MAX_SAFE_PIXELS
    assert VLM_JPEG_QUALITY_PDF_PATH == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_PDF_PATH
    assert VLM_JPEG_QUALITY_GROUNDED == DEFAULT_RASTERIZER_VLM_JPEG_QUALITY_GROUNDED
    assert EMBED_JPEG_QUALITY_PDF == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_PDF
    assert EMBED_JPEG_QUALITY_IMAGE == DEFAULT_RASTERIZER_EMBED_JPEG_QUALITY_IMAGE
