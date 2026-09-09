from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from PIL import Image

from omniscribe.core.document import BBox, DenseMode
from omniscribe.core.imaging.page_preprocess import PagePreprocessingOptions
from omniscribe.core.workflows.stages.conversion import HybridConverter
from omniscribe.core.workflows.stages.layout import (
    HybridLayoutDetector,
    decode_chunk_bytes,
)
from tests.core.test_pipeline import _make_tiny_b64_image, _StubAligner, _StubPDF


class _PreprocessResult:
    def __init__(
        self, images: dict[int, str], metadata: dict[int, dict[str, object]]
    ) -> None:
        self.images = images
        self.metadata = metadata


class _RecordingPreprocessor:
    def __init__(self) -> None:
        self.called = False

    def preprocess(self, images: dict[int, str], options: Any) -> _PreprocessResult:
        self.called = True
        return _PreprocessResult(images, {0: {"preprocessed": True}})


class TestHybridConverter:
    async def test_convert_returns_images_and_metadata(self) -> None:
        pdf = _StubPDF(n_pages=3)
        converter = HybridConverter(pdf_handler=pdf)  # type: ignore[arg-type]
        images, page_nums, metadata = await converter.convert(
            input_path="test.pdf",
            dpi=150,
            max_image_dim=1024,
            pages=None,
            preprocessing_options=None,
            progress=None,
        )
        assert page_nums == [0, 1, 2]
        assert set(images.keys()) == {0, 1, 2}
        assert metadata == {}

    async def test_convert_applies_page_range(self) -> None:
        pdf = _StubPDF(n_pages=5)
        converter = HybridConverter(pdf_handler=pdf)  # type: ignore[arg-type]
        images, page_nums, _ = await converter.convert(
            input_path="test.pdf",
            dpi=150,
            max_image_dim=1024,
            pages="1-2,4",
            preprocessing_options=None,
            progress=None,
        )
        assert page_nums == [0, 1, 3]
        assert set(images.keys()) == {0, 1, 3}

    async def test_convert_applies_subset_page_range_not_starting_at_one(self) -> None:
        pdf = _StubPDF(n_pages=10)
        converter = HybridConverter(pdf_handler=pdf)  # type: ignore[arg-type]
        images, page_nums, _ = await converter.convert(
            input_path="test.pdf",
            dpi=150,
            max_image_dim=1024,
            pages="8-10",
            preprocessing_options=None,
            progress=None,
        )
        assert page_nums == [7, 8, 9]
        assert set(images.keys()) == {7, 8, 9}

    async def test_convert_with_preprocessing_enabled(self) -> None:
        pdf = _StubPDF(n_pages=2)
        preprocessor = _RecordingPreprocessor()
        converter = HybridConverter(pdf_handler=pdf, page_preprocessor=preprocessor)  # type: ignore[arg-type]
        options = PagePreprocessingOptions(enabled=True)
        images, _page_nums, metadata = await converter.convert(
            input_path="test.pdf",
            dpi=150,
            max_image_dim=1024,
            pages=None,
            preprocessing_options=options,
            progress=None,
        )
        assert preprocessor.called is True
        assert metadata == {0: {"preprocessed": True}}
        assert len(images) == 2

    async def test_convert_with_preprocessing_disabled(self) -> None:
        pdf = _StubPDF(n_pages=2)
        preprocessor = _RecordingPreprocessor()
        converter = HybridConverter(pdf_handler=pdf, page_preprocessor=preprocessor)  # type: ignore[arg-type]
        options = PagePreprocessingOptions(enabled=False)
        _images, _page_nums, metadata = await converter.convert(
            input_path="test.pdf",
            dpi=150,
            max_image_dim=1024,
            pages=None,
            preprocessing_options=options,
            progress=None,
        )
        assert preprocessor.called is False
        assert metadata == {}

    async def test_convert_progress_notifications(self) -> None:
        events: list[tuple[str, int, int]] = []

        async def cb(stage: str, cur: int, tot: int, msg: str) -> None:
            events.append((stage, cur, tot))

        pdf = _StubPDF(n_pages=2)
        converter = HybridConverter(pdf_handler=pdf)  # type: ignore[arg-type]
        await converter.convert(
            input_path="test.pdf",
            dpi=150,
            max_image_dim=1024,
            pages=None,
            preprocessing_options=None,
            progress=cb,
        )
        assert ("convert", 0, 1) in events
        assert ("convert", 1, 1) in events

    def test_collect_batched_images_fallback(self) -> None:
        # For non-PDFHandler duck-typed object, falls back to convert_to_images
        mock_handler = MagicMock()
        mock_handler.convert_to_images.return_value = {0: "fake_b64"}
        converter = HybridConverter(pdf_handler=mock_handler)
        result = converter.collect_batched_images(
            input_path="doc.pdf",
            dpi=200,
            max_image_dim=1024,
            pages=None,
            batch_size=8,
        )
        assert result == {0: "fake_b64"}
        mock_handler.convert_to_images.assert_called_once_with(
            "doc.pdf", dpi=200, max_image_dim=1024
        )


class TestHybridLayoutDetector:
    def test_decode_chunk_bytes_populates_callback(self) -> None:
        b64_0 = _make_tiny_b64_image()
        b64_1 = _make_tiny_b64_image()
        images = {0: b64_0, 1: b64_1}
        seen: dict[int, Image.Image] = {}

        raw_bytes = decode_chunk_bytes(images, [0, 1], on_decoded=seen.__setitem__)
        assert len(raw_bytes) == 2
        assert set(seen.keys()) == {0, 1}
        assert isinstance(seen[0], Image.Image)

    async def test_detect_layout_basic(self) -> None:
        aligner = _StubAligner(boxes_per_page=[(0.1, 0.1, 0.9, 0.2)])
        detector = HybridLayoutDetector(aligner=aligner)  # type: ignore[arg-type]
        images = {0: _make_tiny_b64_image()}
        pages = await detector.detect_layout(
            images_dict=images,
            page_nums=[0],
            progress=None,
        )
        assert pages == {0: [((0.1, 0.1, 0.9, 0.2), "")]}

    async def test_detect_layout_with_cache_callbacks(self) -> None:
        cache: dict[int, Image.Image] = {}
        aligner = _StubAligner(boxes_per_page=[(0.1, 0.1, 0.9, 0.2)])
        detector = HybridLayoutDetector(aligner=aligner)  # type: ignore[arg-type]
        images = {0: _make_tiny_b64_image()}
        pages = await detector.detect_layout(
            images_dict=images,
            page_nums=[0],
            progress=None,
            decoded_put=cache.__setitem__,
            decoded_get=cache.get,
        )
        assert pages == {0: [((0.1, 0.1, 0.9, 0.2), "")]}
        assert 0 in cache

    async def test_detect_layout_with_whitespace_recall(self) -> None:
        class _Booster:
            def __init__(self) -> None:
                self.candidates_dropped = 2

            def supplement(self, image: Any, boxes: list[BBox]) -> list[BBox]:
                return [(0.05, 0.05, 0.8, 0.09)]

        aligner = _StubAligner(boxes_per_page=[(0.1, 0.1, 0.9, 0.2)])
        booster = _Booster()
        detector = HybridLayoutDetector(aligner=aligner, recall_booster=booster)  # type: ignore[arg-type]
        images = {0: _make_tiny_b64_image()}
        pages = await detector.detect_layout(
            images_dict=images,
            page_nums=[0],
            progress=None,
        )
        # Extra box sorts before the original box
        boxes = [box for box, _ in pages[0]]
        assert boxes == [(0.05, 0.05, 0.8, 0.09), (0.1, 0.1, 0.9, 0.2)]

    async def test_detect_layout_with_text_layer_recall(self) -> None:
        class _TextLayer:
            def __init__(self) -> None:
                self.opened = False
                self.closed = False
                self.candidates_dropped = 1

            def open(self, path: str) -> bool:
                self.opened = True
                return True

            def close(self) -> None:
                self.closed = True

            def supplement(self, page_num: int, boxes: list[BBox]) -> list[BBox]:
                return [(0.01, 0.01, 0.5, 0.05)]

        tl = _TextLayer()
        tl.open("test.pdf")
        aligner = _StubAligner(boxes_per_page=[(0.1, 0.1, 0.9, 0.2)])
        detector = HybridLayoutDetector(aligner=aligner, text_layer_recall=tl)  # type: ignore[arg-type]
        images = {0: _make_tiny_b64_image()}
        pages = await detector.detect_layout(
            images_dict=images,
            page_nums=[0],
            progress=None,
        )
        assert tl.opened is True
        assert tl.closed is True
        boxes = [box for box, _ in pages[0]]
        assert boxes == [(0.01, 0.01, 0.5, 0.05), (0.1, 0.1, 0.9, 0.2)]

    def test_select_dense_pages(self) -> None:
        aligner = _StubAligner()
        detector = HybridLayoutDetector(aligner=aligner)  # type: ignore[arg-type]
        structured = {
            0: [((0.1, 0.1, 0.9, 0.2), "")] * 10,
            1: [((0.1, 0.1, 0.9, 0.2), "")] * 2,
        }

        assert detector.select_dense_pages(
            structured, [0, 1], dense_mode=DenseMode.AUTO, dense_threshold=5
        ) == {0}
        assert detector.select_dense_pages(
            structured, [0, 1], dense_mode=DenseMode.ALWAYS, dense_threshold=50
        ) == {0, 1}
        assert (
            detector.select_dense_pages(
                structured, [0, 1], dense_mode=DenseMode.NEVER, dense_threshold=1
            )
            == set()
        )
        assert detector.select_dense_pages(
            structured, [0, 1], dense_mode=DenseMode.ALWAYS, dense_threshold=50
        ) == {0, 1}
