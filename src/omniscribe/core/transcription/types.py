"""Data types and intermediate representation for voice transcription."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from omniscribe.core.document import DocumentBlock, DocumentPage, DocumentResult


def logprob_to_confidence(avg_logprob: float | None) -> float | None:
    """Convert a log-domain ``avg_logprob`` into a ``[0, 1]`` confidence.

    Whisper reports per-segment ``avg_logprob`` (log probability), which is
    negative for anything short of certainty. Storing it directly as
    ``confidence`` produced out-of-domain values (e.g. ``-0.15``) that broke
    downstream consumers expecting ``0..1``; the proper confidence is
    ``exp(avg_logprob)``. ``None`` passes through untouched.
    """
    if avg_logprob is None:
        return None
    return math.exp(avg_logprob)


class TranscriptionError(Exception):
    """Base exception for transcription execution errors."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class TranscriptionSegment:
    """Individual timed text segment from voice transcription."""

    id: int
    start: float
    end: float
    text: str
    confidence: float | None = None
    words: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class TranscriptionResult:
    """Complete transcript IR with segments, duration, and metadata."""

    text: str
    language: str | None = None
    duration: float | None = None
    segments: list[TranscriptionSegment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_document_result(self) -> DocumentResult:
        """Convert transcription result to canonical OmniScribe `DocumentResult`.

        Maps transcript segments into DocumentBlocks inside a DocumentPage,
        attaching timestamp and timing metadata for downstream processors/exporters.
        """
        blocks: list[DocumentBlock] = []

        if self.segments:
            for idx, seg in enumerate(self.segments):
                block = DocumentBlock(
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    text=seg.text.strip(),
                    kind="speech",
                    confidence=seg.confidence,
                    source_processor="voice_transcription",
                    reading_order=idx,
                    metadata={
                        "start_time": seg.start,
                        "end_time": seg.end,
                        "duration": round(seg.end - seg.start, 3),
                        "words": seg.words,
                    },
                )
                blocks.append(block)
        elif self.text.strip():
            blocks.append(
                DocumentBlock(
                    bbox=(0.0, 0.0, 1.0, 1.0),
                    text=self.text.strip(),
                    kind="speech",
                    source_processor="voice_transcription",
                    reading_order=0,
                )
            )

        page = DocumentPage(
            page_index=0,
            blocks=blocks,
            metadata={
                "media_type": "audio",
                "language": self.language,
                "duration": self.duration,
                **self.metadata,
            },
        )

        return DocumentResult(pages=[page])
