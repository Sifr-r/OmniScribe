"""Tests for :mod:`omniscribe.core.ocr_quality.orchestrator`."""

from __future__ import annotations

import pytest
from PIL import Image

from omniscribe.core.document import DocumentBlock
from omniscribe.core.ocr_quality import orchestrator
from omniscribe.core.ocr_quality.config import OCrQualitySettings
from omniscribe.core.ocr_quality.types import TrustFlag


def _block(text: str, confidence: float = 0.8, bbox=None) -> DocumentBlock:
    return DocumentBlock(
        bbox=bbox or [0.0, 0.0, 0.5, 0.05],  # type: ignore[arg-type]
        text=text,
        confidence=confidence,
    )


def _image(size=(200, 200), color=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", size, color)


class TestPassthrough:
    def test_empty_blocks_returns_empty(self):
        assert orchestrator.run([], None, OCrQualitySettings(), model_id="x") == []

    def test_all_off_returns_blocks_with_none_trust(self):
        blocks = [_block("hello")]
        settings = OCrQualitySettings()
        assert settings.any_submodule_enabled() is False
        out = orchestrator.run(blocks, None, settings, model_id="x")
        assert len(out) == 1
        assert out[0].text == "hello"
        assert out[0].trust_score is None
        assert out[0].trust_flags is None


class TestWatermarkEnabled:
    def test_blocks_unaffected_when_no_hit(self):
        img = _image()
        blocks = [_block("hello", bbox=(0.0, 0.0, 0.5, 0.05))]
        settings = OCrQualitySettings(watermark_enabled=True)
        out = orchestrator.run(blocks, img, settings, model_id="x")
        # No watermark hit, so no WATERMARK_HIT flag and trust_score still set.
        assert TrustFlag.WATERMARK_HIT.value not in (out[0].trust_flags or ())


class TestCalibrationEnabled:
    def test_unknown_model_identity(self):
        blocks = [_block("hello", confidence=0.7)]
        settings = OCrQualitySettings(calibration_enabled=True)
        out = orchestrator.run(blocks, None, settings, model_id="nonexistent-xyz")
        # Identity calibration → trust_score ≈ 0.7
        assert out[0].trust_score is not None
        assert abs(out[0].trust_score - 0.7) < 0.1

    def test_block_without_confidence_still_scored(self):
        b = DocumentBlock(bbox=(0.0, 0.0, 0.5, 0.05), text="hi", confidence=None)
        settings = OCrQualitySettings(calibration_enabled=True)
        out = orchestrator.run([b], None, settings, model_id="x")
        # confidence defaults to 0.0 → trust_score == 0.0
        assert out[0].trust_score == pytest.approx(0.0)

    def test_below_threshold_blocks_reported_in_event(self, caplog):
        import logging

        caplog.set_level(
            logging.DEBUG, logger="omniscribe.core.ocr_quality.events"
        )
        blocks = [
            _block("low", confidence=0.2),
            _block("high", confidence=0.9),
        ]
        settings = OCrQualitySettings(calibration_enabled=True)
        orchestrator.run(blocks, None, settings, model_id="x")
        decisions = [
            r.getMessage()
            for r in caplog.records
            if "sub_module=orchestrator" in r.getMessage()
        ]
        assert decisions, "orchestrator event missing"
        assert any("below_threshold" in d and "1/2" in d for d in decisions)


class TestHallucinationEnabled:
    def test_clean_text_no_flags(self):
        blocks = [_block("This is a clean sentence.", bbox=(0.0, 0.0, 0.5, 0.05))]
        settings = OCrQualitySettings(hallucination_enabled=True)
        out = orchestrator.run(
            blocks,
            _image(),
            settings,
            model_id="x",
            page_size=(200, 200),
        )
        assert (
            out[0].trust_flags is None or "hallucination_risk" not in out[0].trust_flags
        )

    def test_repetition_flags_block(self):
        blocks = [_block("abcdab" * 20, bbox=(0.0, 0.0, 0.5, 0.05))]
        settings = OCrQualitySettings(hallucination_enabled=True)
        out = orchestrator.run(
            blocks,
            _image(),
            settings,
            model_id="x",
            page_size=(200, 200),
        )
        assert out[0].trust_flags is not None
        assert "hallucination_risk" in out[0].trust_flags


class TestFailOpen:
    def test_submodule_exception_does_not_crash(self, monkeypatch):
        # Force calibration.calibrate to blow up — orchestrator must swallow.
        from omniscribe.core.ocr_quality import calibration
        from omniscribe.core.ocr_quality import orchestrator as orch

        def boom(raw, model_id):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(calibration, "calibrate", boom)
        monkeypatch.setattr(orch, "calibration", calibration)

        blocks = [_block("hello", confidence=0.7)]
        settings = OCrQualitySettings(calibration_enabled=True)
        # Should not raise.
        out = orchestrator.run(blocks, None, settings, model_id="x")
        assert len(out) == 1
        assert out[0].text == "hello"
