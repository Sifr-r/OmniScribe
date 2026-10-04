"""Local document intelligence intermediate representation.

The public pipeline still writes legacy ``{page: [(bbox, text)]}`` structures,
but document processors need a richer handoff object for ordering, quality
metadata, and future extraction/export features. Keep bboxes normalized in
``0..1`` here; PDF coordinate conversion belongs at the output writer boundary.

The ``to_dict`` / ``from_dict`` pair on :class:`DocumentBlock`,
:class:`DocumentPage`, and :class:`DocumentResult` is the serialization
boundary for the rich document artifact: it round-trips page index, geometry,
block kind, confidence, reading order, per-block metadata, trust fields, and
page size/metadata losslessly, and raises :class:`DocumentSerializationError`
on structurally invalid input instead of degrading to an empty document.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from omniscribe.core.errors import OmniScribeError

if TYPE_CHECKING:
    from omniscribe.core.block_tree import DocumentTree

#: Envelope version for the stored rich document artifact. Bump only when a
#: change breaks ``DocumentResult.from_dict`` on previously-written payloads.
DOCUMENT_SERIALIZATION_VERSION = 1

#: Key marking a base64-encoded ``bytes`` value inside a serialized metadata
#: mapping. JSON has no binary type, so raw bytes (e.g. the grounded engine's
#: ``image_bytes``) travel under this tag and are restored on decode.
_BYTES_TAG = "__omniscribe_bytes_b64__"


class DocumentSerializationError(OmniScribeError):
    """Raised when a document payload cannot be serialized or restored.

    The ``from_dict`` family raises it for missing keys, wrong types, and
    out-of-range geometry; the metadata projector raises it for values JSON
    cannot represent. Nothing here degrades silently to an empty document —
    a corrupt artifact must be visible as a typed error.
    """


class PipelineMode(StrEnum):
    HYBRID = "hybrid"
    GROUNDED = "grounded"


class DenseMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class SpellcheckMode(StrEnum):
    NONE = "none"
    AR = "ar"
    EN_US = "en-US"
    DE = "de"
    ES = "es"
    FR = "fr"


BBox = tuple[float, float, float, float]


@dataclass(slots=True)
class DocumentBlock:
    bbox: BBox
    text: str
    kind: str = "text"
    confidence: float | None = None
    source_processor: str = "ocr"
    reading_order: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    # OCR quality trust-layer outputs. ``None`` when the trust layer is
    # disabled (Phase 1 default). When populated, ``trust_score`` is in
    # ``[0, 1]`` and ``trust_flags`` is a sorted tuple of string flags.
    trust_score: float | None = None
    trust_flags: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize one block for the rich document artifact."""
        return {
            "bbox": list(self.bbox),
            "text": self.text,
            "kind": self.kind,
            "confidence": self.confidence,
            "source_processor": self.source_processor,
            "reading_order": self.reading_order,
            "metadata": _encode_metadata(
                self.metadata, path=f"block[{self.text[:32]!r}]"
            ),
            "trust_score": self.trust_score,
            "trust_flags": list(self.trust_flags)
            if self.trust_flags is not None
            else None,
        }

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, path: str = "block"
    ) -> DocumentBlock:
        """Rebuild a block from :meth:`to_dict` output.

        Raises :class:`DocumentSerializationError` when the payload is not a
        mapping, the geometry is unusable, or a scalar field has the wrong
        type.
        """
        payload = _require_mapping(data, path)
        trust_flags = _require_optional_str_sequence(
            payload.get("trust_flags"), f"{path}.trust_flags"
        )
        return cls(
            bbox=_bbox_from_payload(payload.get("bbox"), path=f"{path}.bbox"),
            text=_require_str(payload.get("text"), f"{path}.text"),
            kind=_require_str(payload.get("kind", "text"), f"{path}.kind"),
            confidence=_optional_float(payload.get("confidence"), f"{path}.confidence"),
            source_processor=_require_str(
                payload.get("source_processor", "ocr"), f"{path}.source_processor"
            ),
            reading_order=_optional_int(
                payload.get("reading_order"), f"{path}.reading_order"
            ),
            metadata=_decode_metadata(payload.get("metadata", {}), f"{path}.metadata"),
            trust_score=_optional_float(
                payload.get("trust_score"), f"{path}.trust_score"
            ),
            trust_flags=trust_flags,
        )


@dataclass(slots=True)
class DocumentPage:
    page_index: int
    blocks: list[DocumentBlock] = field(default_factory=list)
    width: int | None = None
    height: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text.strip())

    def to_dict(self) -> dict[str, Any]:
        """Serialize one page (geometry, size, blocks, metadata)."""
        return {
            "page_index": self.page_index,
            "blocks": [block.to_dict() for block in self.blocks],
            "width": self.width,
            "height": self.height,
            "metadata": _encode_metadata(
                self.metadata, path=f"page[{self.page_index}]"
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, path: str = "page") -> DocumentPage:
        """Rebuild a page from :meth:`to_dict` output."""
        payload = _require_mapping(data, path)
        raw_blocks = _require_sequence(payload.get("blocks", []), f"{path}.blocks")
        page_index = _require_int(payload.get("page_index"), f"{path}.page_index")
        if page_index < 0:
            raise DocumentSerializationError(
                f"Expected a non-negative page index at {path}.page_index",
                details={"page_index": page_index},
            )
        return cls(
            page_index=page_index,
            blocks=[
                DocumentBlock.from_dict(block, path=f"{path}.blocks[{i}]")
                for i, block in enumerate(raw_blocks)
            ],
            width=_optional_size(payload.get("width"), f"{path}.width"),
            height=_optional_size(payload.get("height"), f"{path}.height"),
            metadata=_decode_metadata(payload.get("metadata", {}), f"{path}.metadata"),
        )


@dataclass(slots=True)
class DocumentResult:
    """Canonical in-memory handoff for optional document processors.

    Pages are zero-indexed to match the existing OCR pipeline dictionaries.
    Blocks are intentionally mutable: processors can reorder blocks, annotate
    metadata, or rewrite text before ``to_pages_data`` feeds the PDF writer.
    """

    pages: list[DocumentPage]
    source_path: str | None = None
    tree: DocumentTree | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize the whole result, including the structural tree.

        ``tree`` is the processor-built :class:`DocumentTree` (tables,
        sections, figures, equations). It is omitted as ``None`` when the run
        produced pages only; :meth:`from_dict` then restores ``tree=None``
        and callers fall back to :func:`~omniscribe.core.block_tree.from_document_result`.
        """
        return {
            "pages": [page.to_dict() for page in self.pages],
            "source_path": self.source_path,
            "tree": self.tree.to_dict() if self.tree is not None else None,
        }

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, path: str = "document"
    ) -> DocumentResult:
        """Rebuild a result from :meth:`to_dict` output.

        Raises :class:`DocumentSerializationError` for a non-mapping payload,
        a malformed page list, or a structurally broken ``tree`` — a partially
        readable document is never returned as an empty one.
        """
        payload = _require_mapping(data, path)
        raw_pages = _require_sequence(payload.get("pages"), f"{path}.pages")
        tree: DocumentTree | None = None
        raw_tree = payload.get("tree")
        if raw_tree is not None:
            # Local import: ``core.block_tree`` is the consumer side of the
            # handoff and importing it at module scope would couple the two
            # core modules in both directions.
            from omniscribe.core.block_tree import DocumentTree

            tree_payload = _require_mapping(raw_tree, f"{path}.tree")
            try:
                tree = DocumentTree.from_dict(dict(tree_payload))
            except (KeyError, TypeError, ValueError) as exc:
                raise DocumentSerializationError(
                    f"Malformed document tree at {path}.tree",
                    details={"error": str(exc)},
                ) from exc
        source_path = payload.get("source_path")
        if source_path is not None and not isinstance(source_path, str):
            raise DocumentSerializationError(
                f"Expected a string or null at {path}.source_path"
            )
        return cls(
            pages=[
                DocumentPage.from_dict(page, path=f"{path}.pages[{i}]")
                for i, page in enumerate(raw_pages)
            ],
            source_path=source_path,
            tree=tree,
        )

    @classmethod
    def from_pages_data(
        cls,
        pages_data: Mapping[int, Sequence[tuple[Sequence[float], str]]],
        *,
        source_path: str | None = None,
        source_processor: str = "ocr",
        confidence_fn: Callable[[str], float] | None = None,
    ) -> DocumentResult:
        """Build a result from legacy ``{page: [(bbox, text)]}`` payloads.

        The conversion validates every bbox up front so downstream processors can
        assume normalized ``[x0, y0, x1, y1]`` geometry. Invalid or pixel-space
        boxes raise ``ValueError`` instead of being embedded silently.

        ``confidence_fn`` (e.g. the OCR workflow's ``_estimate_confidence``)
        populates ``DocumentBlock.confidence`` at build time so the trust
        layer and repair triggers consume real signal instead of a default 0.0.
        """

        pages: list[DocumentPage] = []
        for page_index in sorted(pages_data):
            blocks = [
                DocumentBlock(
                    bbox=_normalize_bbox(bbox),
                    text=text,
                    source_processor=source_processor,
                    reading_order=reading_order,
                    confidence=(
                        confidence_fn(text)
                        if confidence_fn is not None and text.strip()
                        else None
                    ),
                )
                for reading_order, (bbox, text) in enumerate(pages_data[page_index])
            ]
            pages.append(DocumentPage(page_index=page_index, blocks=blocks))
        return cls(pages=pages, source_path=source_path)

    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())

    def to_pages_data(self) -> dict[int, list[tuple[BBox, str]]]:
        """Convert back to legacy ``{page: [(bbox, text)]}`` for output writers.

        When every block on a page carries a ``reading_order`` annotation
        (e.g. set by :class:`ReadingOrderProcessor`), blocks are emitted in
        that order so the PDF text layer respects processor-assigned reading
        order even if the block list was not physically sorted.
        """
        result: dict[int, list[tuple[BBox, str]]] = {}
        for page in self.pages:
            blocks = page.blocks
            if blocks and all(b.reading_order is not None for b in blocks):
                blocks = sorted(blocks, key=lambda b: b.reading_order or 0)
            result[page.page_index] = [(block.bbox, block.text) for block in blocks]
        return result


def _require_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DocumentSerializationError(
            f"Expected an object at {path}, got {type(value).__name__}"
        )
    for key in value:
        if not isinstance(key, str):
            raise DocumentSerializationError(
                f"Expected string keys at {path}, got {type(key).__name__}"
            )
    return cast("Mapping[str, Any]", value)


def _require_sequence(value: object, path: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise DocumentSerializationError(
            f"Expected an array at {path}, got {type(value).__name__}"
        )
    return value


def _require_str(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise DocumentSerializationError(
            f"Expected a string at {path}, got {type(value).__name__}"
        )
    return value


def _require_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DocumentSerializationError(
            f"Expected an integer at {path}, got {type(value).__name__}"
        )
    return value


def _optional_int(value: object, path: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, path)


def _optional_float(value: object, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DocumentSerializationError(
            f"Expected a number or null at {path}, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise DocumentSerializationError(
            f"Expected a finite number at {path}", details={"value": repr(value)}
        )
    return number


def _optional_size(value: object, path: str) -> int | None:
    size = _optional_int(value, path)
    if size is not None and size <= 0:
        raise DocumentSerializationError(
            f"Expected a positive size at {path}", details={"value": size}
        )
    return size


def _require_optional_str_sequence(value: object, path: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    items = _require_sequence(value, path)
    return tuple(_require_str(item, f"{path}[{i}]") for i, item in enumerate(items))


def _bbox_from_payload(value: object, *, path: str) -> BBox:
    values = _require_sequence(value, path)
    if len(values) != 4:
        raise DocumentSerializationError(
            f"Expected 4 bbox values at {path}, got {len(values)}"
        )
    numbers: list[float] = []
    for i, item in enumerate(values):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise DocumentSerializationError(
                f"Expected a number at {path}[{i}], got {type(item).__name__}"
            )
        number = float(item)
        if not math.isfinite(number):
            raise DocumentSerializationError(
                f"Expected a finite number at {path}[{i}]",
                details={"value": repr(item)},
            )
        numbers.append(number)
    try:
        return _normalize_bbox(numbers)
    except ValueError as exc:
        raise DocumentSerializationError(
            f"Invalid bbox at {path}", details={"error": str(exc)}
        ) from exc


def _encode_metadata(metadata: Mapping[str, object], *, path: str) -> dict[str, Any]:
    """Project a metadata mapping onto JSON-representable values.

    Tuples become lists (JSON has no tuple type) and ``bytes`` are base64
    tagged; anything else raises rather than being stringified, so a lost
    field can never masquerade as a faithful round-trip.
    """
    return {
        key: _encode_json_value(value, path=f"{path}.{key}")
        for key, value in _require_mapping(metadata, path).items()
    }


def _encode_json_value(value: object, *, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DocumentSerializationError(
                f"Non-finite float at {path}", details={"value": repr(value)}
            )
        return value
    if isinstance(value, bytes):
        return {_BYTES_TAG: base64.b64encode(value).decode("ascii")}
    if isinstance(value, Mapping):
        return {
            str(key): _encode_json_value(item, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _encode_json_value(item, path=f"{path}[{i}]")
            for i, item in enumerate(value)
        ]
    raise DocumentSerializationError(
        f"Value at {path} is not JSON-representable",
        details={"type": type(value).__name__},
    )


def _decode_metadata(value: object, path: str) -> dict[str, object]:
    decoded = _decode_json_value(value, path=path)
    if not isinstance(decoded, dict):
        raise DocumentSerializationError(
            f"Expected an object at {path}, got {type(decoded).__name__}"
        )
    return decoded


def _decode_json_value(value: object, *, path: str) -> object:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        if len(value) == 1 and _BYTES_TAG in value:
            encoded = value[_BYTES_TAG]
            if not isinstance(encoded, str):
                raise DocumentSerializationError(
                    f"Expected base64 text at {path}.{_BYTES_TAG}"
                )
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise DocumentSerializationError(
                    f"Invalid base64 payload at {path}.{_BYTES_TAG}",
                    details={"error": str(exc)},
                ) from exc
        return {
            key: _decode_json_value(item, path=f"{path}.{key}")
            for key, item in _require_mapping(value, path).items()
        }
    if isinstance(value, list):
        return [
            _decode_json_value(item, path=f"{path}[{i}]")
            for i, item in enumerate(value)
        ]
    raise DocumentSerializationError(
        f"Unsupported value at {path}", details={"type": type(value).__name__}
    )


def _normalize_bbox(bbox: Sequence[float]) -> BBox:
    if len(bbox) != 4:
        raise ValueError(f"Expected bbox with 4 values, got {len(bbox)}")
    x0, y0, x1, y1 = (float(value) for value in bbox)
    eps = 1e-3
    if not (
        -eps <= x0 <= 1.0 + eps
        and -eps <= y0 <= 1.0 + eps
        and -eps <= x1 <= 1.0 + eps
        and -eps <= y1 <= 1.0 + eps
    ):
        raise ValueError(f"Expected normalized bbox in 0..1, got {(x0, y0, x1, y1)!r}")
    x0 = max(0.0, min(1.0, x0))
    y0 = max(0.0, min(1.0, y0))
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    if x1 <= x0:
        x1 = min(1.0, max(x0 + 1e-4, 1e-4))
        if x1 <= x0:
            x0 = max(0.0, x1 - 1e-4)
    if y1 <= y0:
        y1 = min(1.0, max(y0 + 1e-4, 1e-4))
        if y1 <= y0:
            y0 = max(0.0, y1 - 1e-4)
    return (x0, y0, x1, y1)
