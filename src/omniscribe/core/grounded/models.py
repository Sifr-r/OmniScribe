"""Data classes and protocols for grounded OCR backends.

Three pieces live here:

- :data:`ProgressCallback` / :data:`WarningCallback` — type aliases
  shared by every grounded backend. Defined once so importers don't
  each pick a slightly-different signature.
- :class:`GroundedBlock` / :class:`GroundedResponse` — the normalized
  shape every backend returns. Consumers (pipeline, PDF embedding)
  rely on this being a flat, deterministic structure: ``blocks``
  is page-ordered, ``page_sizes`` lists every page that was
  successfully rasterized, and ``failed_pages`` lists the indices
  whose OCR call raised (used by the pipeline's ``on_warning``
  surface).
- :class:`GroundedOCRBackend` — async Protocol with one method,
  ``ocr_document``. Any backend that matches this shape plugs into
  :class:`OCRPipeline` without subclassing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

ProgressCallback = Callable[[str, int, int, str], Awaitable[None]]
WarningCallback = Callable[[int, BaseException], Awaitable[None]]


@dataclass
class GroundedBlock:
    bbox: list[float]  # normalized [nx0, ny0, nx1, ny1] in 0..1
    text: str
    page_index: int
    label: str = "text"  # filter: keep "text", drop "image"/"figure"
    image_bytes: bytes | None = None


@dataclass
class GroundedResponse:
    blocks: list[GroundedBlock]
    page_sizes: list[tuple[int, int]] = field(default_factory=list)  # (w, h) per page
    failed_pages: list[int] = field(default_factory=list)


class GroundedOCRBackend(Protocol):
    """Backends that return text WITH layout in one shot (no Surya needed).

    `progress` is optional; callers that don't care about per-page updates
    can omit it. Backends SHOULD emit the `"ocr"` stage with (current,
    total) set to pages-completed / total-pages so the pipeline's progress
    adapter stays aligned with the documented stage set.

    `on_warning` is called once per page whose OCR call raised an
    exception at the backend's per-page isolation boundary. The
    pipeline uses it (alongside `failed_pages` on the response) to
    surface partial failures to the caller.
    """

    async def ocr_document(
        self,
        pdf_path: str,
        progress: ProgressCallback | None = None,
        on_warning: WarningCallback | None = None,
    ) -> GroundedResponse: ...


@runtime_checkable
class RepairableGroundedBackend(Protocol):
    """F1.14 audit fix: typed contract for backends that expose a per-crop
    re-OCR entry point.

    The repair loop in
    :class:`omniscribe.core.workflows.grounded.GroundedEngine` only runs
    when a backend can re-OCR a single crop on demand. Before the
    Protocol existed, the gate was ``hasattr(self.grounded_backend,
    "ocr_crop")`` with a ``# type: ignore[attr-defined]`` at the
    call site — a duck-type that the type checker could not see and
    that a future refactor could silently break. The Protocol lets
    call sites use ``isinstance(self.grounded_backend,
    RepairableGroundedBackend)`` so mypy checks the shape at
    construction time instead of the first repair attempt.

    Backends that cannot repair (e.g. a single-shot VLM with no
    per-crop mode) do NOT need to match this Protocol; the grounded
    engine falls back to a no-op repair path.

    `image_base64` is the base64-encoded cropped image, exactly the
    shape the hybrid path uses for ``OCRProcessor.perform_ocr_on_crop``
    — backends that delegate to the same VLM client can pass the
    bytes through unchanged. `bbox` is normalized ``[x0, y0, x1, y1]``
    in ``0..1`` (page coordinates) for diagnostics / logging.
    """

    async def ocr_crop(
        self,
        input_path: str,
        page_index: int,
        bbox: tuple[float, float, float, float] | Sequence[float],
        *,
        previous_text: str = "",
        attempt: int = 1,
    ) -> str: ...


__all__ = [
    "GroundedBlock",
    "GroundedOCRBackend",
    "GroundedResponse",
    "ProgressCallback",
    "RepairableGroundedBackend",
    "WarningCallback",
]
