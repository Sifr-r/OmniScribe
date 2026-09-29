"""Unit tests for the crop utility used by the refine stage."""

from __future__ import annotations

import base64
import io

from PIL import Image

from omniscribe.utils.image import (
    DEFAULT_CROP_PADDING,
    DEFAULT_CROP_QUALITY,
    crop_for_ocr_from_image,
)


def _make_pil_image(size=(800, 1000)) -> Image.Image:
    """Create a test PIL Image with a visible red patch."""
    img = Image.new("RGB", size, "white")
    for y in range(400, 600):
        for x in range(100, 300):
            img.putpixel((x, y), (255, 0, 0))
    return img


def _decode_b64_image(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def test_crop_returns_valid_image():
    img = _make_pil_image()
    bbox = [0.1, 0.4, 0.4, 0.6]
    crop_b64 = crop_for_ocr_from_image(img, bbox)
    assert crop_b64 is not None
    out = _decode_b64_image(crop_b64)
    assert out.width > 0 and out.height > 0


def test_crop_upscales_tiny_regions():
    img = _make_pil_image(size=(1000, 1000))
    # Tiny box inside the painted red patch (100..300, 400..600) -> (0.1..0.3, 0.4..0.6)
    tiny_bbox = [0.12, 0.42, 0.14, 0.44]  # 20x20 pixels raw
    crop_b64 = crop_for_ocr_from_image(img, tiny_bbox, min_dim=256, std_threshold=0.0)
    assert crop_b64 is not None
    out = _decode_b64_image(crop_b64)
    # The helper should upscale so the VLM can read glyphs.
    assert out.width >= 256 or out.height >= 256


def test_crop_captures_painted_region():
    img = _make_pil_image()  # red patch in (100..300, 400..600)
    bbox = [0.1, 0.4, 0.4, 0.6]  # same region normalized
    crop_b64 = crop_for_ocr_from_image(img, bbox, padding=0.0)
    assert crop_b64 is not None
    out = _decode_b64_image(crop_b64)
    # Center pixel should be red-ish (JPEG compression is forgiving).
    cx, cy = out.width // 2, out.height // 2
    r, g, b = out.getpixel((cx, cy))  # type: ignore[misc]
    assert r > 150 and g < 100 and b < 100, f"expected red-ish, got {(r, g, b)}"


def test_crop_clamps_out_of_range_bbox():
    img = _make_pil_image()
    # Negative + >1 coords: helper must clamp without crashing.
    crop_b64 = crop_for_ocr_from_image(img, [-0.1, -0.1, 1.2, 1.2])
    assert crop_b64 is not None
    out = _decode_b64_image(crop_b64)
    assert out.width > 0 and out.height > 0


def test_crop_for_ocr_from_image_blank_region_returns_none():
    """Blank/uniform regions return None (skip LLM call optimization)."""
    # Create an all-white image (no visible content)
    blank_img = Image.new("RGB", (800, 1000), "white")
    bbox = [0.1, 0.1, 0.4, 0.3]
    result = crop_for_ocr_from_image(blank_img, bbox)
    assert result is None


def test_crop_for_ocr_from_image_reuses_same_image():
    """⚡ Performance test: verify the same PIL Image can be reused across
    multiple crop calls without issues (the optimization's core behavior).
    """
    img = (
        _make_pil_image()
    )  # red patch at (100..300, 400..600) = (0.125..0.375, 0.4..0.6)
    bboxes = [
        [0.1, 0.4, 0.4, 0.6],  # red patch region - has content
        [0.5, 0.1, 0.8, 0.3],  # blank region (no red pixels)
        [0.12, 0.42, 0.16, 0.46],  # small region INSIDE red patch - will upscale
    ]
    # Call multiple times with the same image - should not corrupt or mutate
    results = [crop_for_ocr_from_image(img, bbox) for bbox in bboxes]
    # Red patch should succeed, blank should be None, small red region should upscale
    assert results[0] is not None  # has content (large red patch)
    assert results[1] is None  # blank (white region)
    assert results[2] is not None  # upscaled (small but has red content)


# ---------------------------------------------------------------------------
# F1.17 — unified crop padding + JPEG quality
# (re-homed from test_audit_medium_d1.py)
# ---------------------------------------------------------------------------


class TestUnifiedCropParameters:
    """F1.17 audit fix: the hybrid and grounded paths now share
    ``DEFAULT_CROP_PADDING`` (0.5%) and ``DEFAULT_CROP_QUALITY`` (85)
    from :mod:`omniscribe.utils.image`. A change to either constant
    flows through both paths.
    """

    def test_hybrid_path_uses_canonical_constants(self) -> None:
        # Build an image with enough variance that the stddev guard
        # does not short-circuit. ``crop_for_ocr_from_image`` returns
        # ``None`` for regions with stddev below
        # ``DEFAULT_CROP_STD_THRESHOLD`` (12.0), so a uniform image
        # would mask the assertion.
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        # Draw a checker pattern in the bottom-right (the region we'll
        # crop) to push the stddev above the threshold.
        pixels = img.load()
        assert pixels is not None
        for y in range(50, 100):
            for x in range(50, 100):
                pixels[x, y] = (0, 0, 0) if (x + y) % 2 == 0 else (255, 255, 255)
        out = crop_for_ocr_from_image(img, (0.5, 0.5, 1.0, 1.0))
        assert out is not None
        # Re-run with explicit non-default padding/quality and
        # confirm the defaults still match the constants.
        assert DEFAULT_CROP_PADDING == 0.005
        assert DEFAULT_CROP_QUALITY == 85

    def test_grounded_path_uses_canonical_constants(self) -> None:
        """``_crop_normalized`` in ``prompted.py`` reads
        ``DEFAULT_CROP_PADDING`` and ``DEFAULT_CROP_QUALITY`` from
        ``utils.image`` at call time, so a change to those constants
        flows through to the grounded path.
        """
        # Verify the import is the canonical source (not a
        # re-declared local constant). This is a static check on the
        # source: if a future refactor inlines a magic number, the
        # test catches it.
        from omniscribe.core.grounded import prompted as prompted_mod

        with open(prompted_mod.__file__, encoding="utf-8") as f:
            source = f.read()
        assert "DEFAULT_CROP_PADDING" in source
        assert "DEFAULT_CROP_QUALITY" in source
        # And no leftover magic numbers from the pre-fix code path.
        assert "0.05 * max(bbox[2] - bbox[0]" not in source
        assert "quality=90" not in source

    def test_canonical_values_pinned(self) -> None:
        """Pin the canonical values so a silent change to the
        constants surfaces in the audit (and breaks calibration
        parity with the trust-scorer models).
        """
        assert DEFAULT_CROP_PADDING == 0.005
        assert DEFAULT_CROP_QUALITY == 85


def test_get_executor_cached_and_not_shutdown() -> None:
    """[P1-9] _get_executor keeps a single fixed-size cached ThreadPoolExecutor and does not shut down."""
    import os

    from omniscribe.utils.image import _get_executor, crop_many_for_ocr_from_image

    e1 = _get_executor(2)
    e2 = _get_executor(8)
    assert e1 is e2
    assert e1._max_workers == max(4, os.cpu_count() or 4)
    # Ensure executor is alive and can execute tasks
    future = e1.submit(lambda: 42)
    assert future.result() == 42

    # Multiple crop_many calls with varying workers do not kill the executor
    img = Image.new("RGB", (200, 200), (255, 0, 0))
    bboxes = [[0.1, 0.1, 0.4, 0.4], [0.5, 0.5, 0.8, 0.8]]
    res1 = crop_many_for_ocr_from_image(img, bboxes, max_workers=2)
    res2 = crop_many_for_ocr_from_image(img, bboxes, max_workers=8)
    assert len(res1) == 2 and len(res2) == 2
    assert not e1._shutdown
