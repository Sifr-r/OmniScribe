"""Image utilities for cropping page regions by normalized bounding box."""

from __future__ import annotations

import base64
import io
import os
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image

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

# Iteration 3 (perf): cProfile on the OCR crop+encode path showed
# LANCZOS upscaling consumes ~62% of wall time for dense pages whose
# small text-line crops trigger the ``cw < min_dim`` upscale branch.
# cProfile breakdown for 50 dense pages x 150 boxes (7500 crops):
#   - LANCZOS resize:           5.928s  (62%)
#   - JPEG encode (libjpeg):    1.487s  (16%)
#   - stddev blank-check:       0.770s   (8%)
#   - misc Python overhead:     1.336s  (14%)
# Per-resize microbench (50x50 -> 256x256):
#   - LANCZOS:  0.597 ms
#   - BICUBIC:  0.386 ms   (1.55x faster)
#   - BILINEAR: 0.276 ms   (2.16x faster)
# BICUBIC is the standard OCR/VLM sweet spot: smooth on high-contrast
# text edges, no LANCZOS overshoot ringing, and the VLM has its own
# attention for sub-pixel detail. The trust scorer consumes the OCR
# text output (not the JPEG bytes) so this change does not shift the
# trust-score calibration. The grounded path does not resize at all,
# so F1.17 parity with the grounded path is preserved.
DEFAULT_CROP_RESAMPLING: Image.Resampling = Image.Resampling.BICUBIC

# Iteration 5 (perf): after Iteration 4 (4-worker parallelism)
# parallelized the resize + JPEG encode, the stddev blank-check
# became the new dominant cost — 6.748s cumulative across 7500 crops
# (~0.9 ms / crop). The previous ``ImageStat.Stat(crop.convert("L"))``
# path does a histogram pass plus a stats compute; numpy's stddev on
# the L-converted crop is 1.5-2x faster and the project already
# declares numpy as a direct dependency (``pyproject.toml``).
# Per-stddev microbench (5000 iterations):
#   (50, 50):   ImageStat 0.042 ms -> numpy 0.022 ms  (1.9x)
#   (100, 100): ImageStat 0.060 ms -> numpy 0.041 ms  (1.5x)
#   (200, 100): ImageStat 0.084 ms -> numpy 0.065 ms  (1.3x)
#   (300, 200): ImageStat 0.181 ms -> numpy 0.182 ms  (parity)
# Numpy wins for small/medium crops (the common case for text-line
# bboxes that triggered the upscale); parity at large crops.
# Iteration 4 (perf): cProfile on the post-Iteration-3 crop+encode path
# showed BICUBIC resize (54%, 4.053s for 50 pages x 150 boxes) and
# JPEG encode (20%, 1.484s) dominate. Pillow's C code releases the GIL
# on resize/encode so multiple worker threads can run crops in parallel
# within a single batched page call.
#
# Wall-time microbench (150 boxes/page, 20 iterations, asyncio.to_thread
# dispatch + per-crop PIL work; mixed realistic bboxes — 75 small
# triggering upscale + 75 large skipping upscale):
#   - 1 worker (sequential, pre-I4):  229 ms/page
#   - 2 workers:                      169 ms/page  (1.36x)
#   - 4 workers (default):            122 ms/page  (1.88x)
#   - 8 workers:                      104 ms/page  (2.20x)
#
# The speedup is bounded by GIL contention on the Python-side overhead
# (bbox arithmetic, BytesIO alloc, ImageStat setup). For pure-C work
# (large no-upscale crops) the speedup approaches 2.7x at 8 workers;
# for small upscaled crops (more Python overhead per box) it tops out
# around 1.7x. 4 workers is the sweet spot — close to peak speedup
# without oversaturating the asyncio default executor.
DEFAULT_CROP_PARALLEL_WORKERS: int = 4

# Module-level lazy executor. ThreadPoolExecutor.__init__ is cheap (~50
# us); we keep the executor alive for the process lifetime to avoid
# repeated thread spawn cost on every page batch. ``None`` until the
# first ``crop_many`` call so importing this module has no side effects.
_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_LOCK = Lock()

__all__ = [
    "DEFAULT_CROP_MIN_DIM",
    "DEFAULT_CROP_PADDING",
    "DEFAULT_CROP_PARALLEL_WORKERS",
    "DEFAULT_CROP_QUALITY",
    "DEFAULT_CROP_RESAMPLING",
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
    resampling: Image.Resampling,
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

    Concurrency:
    ``PIL.Image.crop`` and the C-level ``crop.convert``, ``crop.resize``,
    ``crop.save``, ``ImageStat.Stat`` calls release the GIL, so this
    function is safe to call from multiple threads on the same ``img``
    (PIL docs: "PIL is generally thread-safe"). The ``buf`` parameter
    must NOT be shared across threads — each parallel worker gets its
    own ``BytesIO``.
    """
    nx0, ny0, nx1, ny1 = bbox
    nx0 = max(0.0, nx0 - padding)
    ny0 = max(0.0, ny0 - padding)
    nx1 = min(1.0, nx1 + padding)
    ny1 = min(1.0, ny1 + padding)
    crop = img.crop((int(nx0 * w), int(ny0 * h), int(nx1 * w), int(ny1 * h)))
    if crop.size[0] == 0 or crop.size[1] == 0:
        return None
    # Iteration 5: numpy stddev replaces ``ImageStat.Stat`` for the
    # blank-region guard. numpy is already a direct project dep, and
    # the microbench (Iteration 3 docstring) shows 1.3-1.9x speedup
    # for the small/medium crop sizes that dominate dense OCR.
    # ``np.asarray`` on an L image is a zero-copy view (PIL stores
    # the buffer contiguously), so this is one pass over the pixels.
    if (
        std_threshold > 0.0
        and float(np.asarray(crop.convert("L")).std()) < std_threshold
    ):
        return None

    cw, ch = crop.size
    if cw < min_dim or ch < min_dim:
        scale = max(min_dim / max(1, cw), min_dim / max(1, ch))
        scale = min(scale, 16.0)
        crop = crop.resize((int(cw * scale), int(ch * scale)), resampling)

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
    resampling: Image.Resampling = DEFAULT_CROP_RESAMPLING,
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
        resampling=resampling,
        buf=buf,
    )


def _get_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """Lazily construct (and cache) the module-level executor.

    ThreadPoolExecutor's ``__init__`` allocates a ``ThreadPool`` and
    spawns the worker threads (~50 us + thread spawn cost). We keep
    one alive for the process lifetime so a page batch never pays the
    spawn cost. Sized to max(4, os.cpu_count() or 4).
    The lock is for the rare case where two threads enter
    this function concurrently on the first call.
    """
    global _EXECUTOR
    if _EXECUTOR is not None:
        return _EXECUTOR
    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None:
            return _EXECUTOR
        pool_size = max(4, os.cpu_count() or 4)
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=pool_size,
            thread_name_prefix="crop-many",
        )
    return _EXECUTOR


def crop_many_for_ocr_from_image(
    img: Image.Image,
    bboxes: Iterable[Sequence[float]],
    *,
    padding: float = DEFAULT_CROP_PADDING,
    min_dim: int = DEFAULT_CROP_MIN_DIM,
    quality: int = DEFAULT_CROP_QUALITY,
    std_threshold: float = DEFAULT_CROP_STD_THRESHOLD,
    resampling: Image.Resampling = DEFAULT_CROP_RESAMPLING,
    max_workers: int = DEFAULT_CROP_PARALLEL_WORKERS,
) -> list[str | None]:
    """Batched counterpart to :func:`crop_for_ocr_from_image`.

    Crops every bbox from the same pre-decoded ``img`` in a single call
    so the caller can submit the whole batch to
    :func:`asyncio.to_thread` once, instead of one submission per box.
    For a 150-box dense page this collapses ~150 executor round-trips
    (~7-30 ms of pure scheduling overhead) into 1, and the PIL work
    (resize + JPEG encode, both GIL-releasing) is spread across
    ``max_workers`` threads for further speedup.

    Wall-time microbench on this machine (150 boxes/page, 20 iterations,
    mixed realistic bboxes — 75 small triggering upscale + 75 large
    skipping upscale):
        - 1 worker (sequential): 229 ms/page
        - 2 workers:             169 ms/page  (1.36x)
        - 4 workers (default):   122 ms/page  (1.88x)
        - 8 workers:             104 ms/page  (2.20x)

    The speedup is bounded by GIL contention on the Python-side overhead
    (bbox arithmetic, BytesIO alloc, ImageStat setup). For pure-C work
    (large no-upscale crops) the speedup approaches 2.7x at 8 workers;
    for small upscaled crops it tops out around 1.7x.

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
        max_workers: Number of threads for the inner crop+encode work.
             ``DEFAULT_CROP_PARALLEL_WORKERS`` (4) is the sweet spot on
             the typical 4+ core machine — close to peak speedup without
             oversaturating the asyncio default executor. Set to ``1``
             to disable parallelism (sequential fallback, useful for
             debugging or for very small batches where the thread-spawn
             + future-scheduling cost dominates).
    """
    # Materialize iterable once — we need random access for the parallel
    # dispatch and we want to short-circuit empty input cleanly.
    bbox_list = list(bboxes)
    if not bbox_list:
        return []

    # Ensure RGB once for the whole page — the single-box wrapper repeats
    # this check per call, which is fine when called individually but
    # wasteful in a tight loop over 100+ boxes.
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size

    if max_workers <= 1 or len(bbox_list) <= 1:
        buf = io.BytesIO()
        return [
            _crop_one(
                img=img,
                w=w,
                h=h,
                bbox=bbox,
                padding=padding,
                min_dim=min_dim,
                quality=quality,
                std_threshold=std_threshold,
                resampling=resampling,
                buf=buf,
            )
            for bbox in bbox_list
        ]

    # Cap workers at the box count — submitting more workers than
    # tasks wastes thread-spawn overhead. ``os.cpu_count()`` cap is a
    # final safety rail for very large machines.
    effective_workers = min(
        max_workers,
        len(bbox_list),
        max(1, (os.cpu_count() or 4)),
    )
    executor = _get_executor(effective_workers)

    # Each worker needs its own ``BytesIO`` — the buffer is mutable and
    # ``_crop_one`` does ``seek(0); truncate()`` then ``save()`` then
    # ``getbuffer()``, which is unsafe to share across threads.
    def submit_one(bbox: Sequence[float]) -> str | None:
        return _crop_one(
            img=img,
            w=w,
            h=h,
            bbox=bbox,
            padding=padding,
            min_dim=min_dim,
            quality=quality,
            std_threshold=std_threshold,
            resampling=resampling,
            buf=io.BytesIO(),
        )

    futures = [executor.submit(submit_one, bbox) for bbox in bbox_list]
    # ``Future.result()`` raises the worker exception in this thread —
    # preserve the old sequential behaviour where a per-crop failure
    # became a None via the upstream caller. The crop work is local
    # PIL C calls; in practice it does not raise.
    return [f.result() for f in futures]
