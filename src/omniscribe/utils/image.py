"""Image utilities for cropping page regions by normalized bounding box."""

from __future__ import annotations

import base64
import io
from collections.abc import Sequence
from typing import TYPE_CHECKING

from PIL import Image, ImageStat

if TYPE_CHECKING:
    from collections.abc import Iterable

# F1.17 audit fix: canonical crop parameters shared by the hybrid and
# grounded paths. The two paths used to disagree (hybrid = 0.5% padding
# + JPEG quality 85, grounded = 5% padding + JPEG quality 90), which
# silently broke the OCR trust-score calibration parity — a block that
# scored 0.6 on the hybrid path might score 0.4 on the grounded path
# purely because of the JPEG/PSNR difference, not because the text
# quality differed. Both paths now use the constants below; the
# grounded backend imports them so a future maintainer changing one
# updates both at once.
DEFAULT_CROP_PADDING: float = 0.005
DEFAULT_CROP_QUALITY: int = 85
DEFAULT_CROP_MIN_DIM: int = 256
DEFAULT_CROP_STD_THRESHOLD: float = 12.0

__all__ = [
    "DEFAULT_CROP_MIN_DIM",
    "DEFAULT_CROP_PADDING",
    "DEFAULT_CROP_QUALITY",
    "DEFAULT_CROP_STD_THRESHOLD",
    "crop_for_ocr_from_image",
    "crop_many_for_ocr_from_image",
]


def _crop_one(
    img: Image.Image,
    w: int,
    h: int,
    bbox: Sequence[float],
    padding: float,
    min_dim: int,
    quality: int,
    std_threshold: float,
    buf: io.BytesIO,
) -> str | None:
    """Inner body of :func:`crop_for_ocr_from_image` — module-private
    so :func:`crop_many_for_ocr_from_image` can share it without paying
    the per-call cost of the public wrapper's ``img.convert("RGB")`` mode
    check (the batched caller already guarantees RGB once).

    Pre-conditions (caller-enforced):
    - ``img.mode == "RGB"``
    - ``(w, h) == img.size``
    - ``buf`` is a cleared, reusable BytesIO (no other threads touch it).

    Returns ``None`` for empty / uniform crops.
    """
    nx0, ny0, nx1, ny1 = bbox
    nx0 = max(0.0, nx0 - padding)
    ny0 = max(0.0, ny0 - padding)
    nx1 = min(1.0, nx1 + padding)
    ny1 = min(1.0, ny1 + padding)
    crop = img.crop((int(nx0 * w), int(ny0 * h), int(nx1 * w), int(ny1 * h)))
    if crop.size[0] == 0 or crop.size[1] == 0:
        return None
    if (
        std_threshold > 0.0
        and ImageStat.Stat(crop.convert("L")).stddev[0] < std_threshold
    ):
        return None

    cw, ch = crop.size
    if cw < min_dim or ch < min_dim:
        scale = max(min_dim / max(1, cw), min_dim / max(1, ch))
        scale = min(scale, 16.0)
        crop = crop.resize(
            (int(cw * scale), int(ch * scale)), Image.Resampling.LANCZOS
        )

    buf.seek(0)
    buf.truncate()
    crop.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getbuffer()).decode("ascii")


def crop_for_ocr_from_image(
    img: Image.Image,
    bbox: Sequence[float],
    *,
    padding: float = DEFAULT_CROP_PADDING,
    min_dim: int = DEFAULT_CROP_MIN_DIM,
    quality: int = DEFAULT_CROP_QUALITY,
    std_threshold: float = DEFAULT_CROP_STD_THRESHOLD,
) -> str | None:
    """Crop a bbox region from a pre-decoded PIL Image and return the
    encoded JPEG — or ``None`` if the region is mostly uniform.

    ⚡ Performance optimization: when processing many boxes from the same
    page (dense-mode OCR or refine stage), decode the page image ONCE and
    pass the PIL Image here. Avoids redundant base64 decoding + PIL open
    for every box, saving ~50-200ms per box on a typical page image.

    For a 150-box dense page, this saves ~7-30 seconds of redundant I/O.

    Args:
        img: Pre-decoded PIL Image (RGB). Caller is responsible for
             decoding; share the same image across multiple crop calls.
        bbox: [nx0, ny0, nx1, ny1] in 0..1 normalized page coordinates.
        padding: Normalized padding added around the bbox before cropping.
        min_dim: Minimum dimension (px) to upscale the crop to.
        quality: JPEG quality for the returned image.
        std_threshold: Stddev threshold for blank-region detection.

    For batch cropping (one call, many boxes, same page), use
    :func:`crop_many_for_ocr_from_image` instead — it amortizes the
    per-call executor round-trip and reuses a single ``BytesIO``.
    """
    # Ensure RGB mode for consistent crop behavior
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    buf = io.BytesIO()
    return _crop_one(
        img=img,
        w=w,
        h=h,
        bbox=bbox,
        padding=padding,
        min_dim=min_dim,
        quality=quality,
        std_threshold=std_threshold,
        buf=buf,
    )


def crop_many_for_ocr_from_image(
    img: Image.Image,
    bboxes: Iterable[Sequence[float]],
    *,
    padding: float = DEFAULT_CROP_PADDING,
    min_dim: int = DEFAULT_CROP_MIN_DIM,
    quality: int = DEFAULT_CROP_QUALITY,
    std_threshold: float = DEFAULT_CROP_STD_THRESHOLD,
) -> list[str | None]:
    """Batched counterpart to :func:`crop_for_ocr_from_image`.

    Crops every bbox from the same pre-decoded ``img`` in a single call
    so the caller can submit the whole batch to
    :func:`asyncio.to_thread` once, instead of one submission per box.
    For a 150-box dense page this collapses ~150 executor round-trips
    (~7-30 ms of pure scheduling overhead) into 1.

    Behaviour is identical to :func:`crop_for_ocr_from_image` per crop:
    - ``None`` is returned for empty / uniform / sub-threshold crops.
    - JPEG quality, padding, min-dim, stddev-threshold defaults match
      the single-box helper.
    - Returned list is parallel to ``bboxes``.

    Args:
        img: Pre-decoded PIL Image (RGB). Must be RGB; if you have a
             non-RGB image, convert it once before calling — the batched
             helper intentionally skips the per-crop mode check that the
             single-box wrapper does, because every box shares the same
             page image.
        bboxes: Iterable of ``[nx0, ny0, nx1, ny1]`` in 0..1 normalized
             page coordinates. May be empty (returns ``[]``).
    """
    # Ensure RGB once for the whole page — the single-box wrapper repeats
    # this check per call, which is fine when called individually but
    # wasteful in a tight loop over 100+ boxes.
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    buf = io.BytesIO()
    results: list[str | None] = []
    for bbox in bboxes:
        results.append(
            _crop_one(
                img=img,
                w=w,
                h=h,
                bbox=bbox,
                padding=padding,
                min_dim=min_dim,
                quality=quality,
                std_threshold=std_threshold,
                buf=buf,
            )
        )
    return results
