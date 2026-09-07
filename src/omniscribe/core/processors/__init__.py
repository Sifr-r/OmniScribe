"""DocumentResult processor interfaces, implementations, and registry."""

from __future__ import annotations

from omniscribe.core.processors.base import (
    LOCAL_DOCUMENT_PROCESSOR_NAMES,
    DocumentProcessor,
    DocumentProcessorFactory,
    DocumentProcessorRegistry,
    ProcessorContract,
    _bbox_area,
    _normalize_space,
    _page_region,
    _structure_kind,
    build_document_processors,
    run_document_processors,
)
from omniscribe.core.processors.layout import LayoutEnrichmentProcessor
from omniscribe.core.processors.quality import QualityAnalysisProcessor
from omniscribe.core.processors.reading_order import ReadingOrderProcessor
from omniscribe.core.processors.section import SectionAnalysisProcessor
from omniscribe.core.processors.structure import StructureAnalysisProcessor
from omniscribe.core.processors.table import (
    TableExtractionProcessor,
    _TableCellRecord,
    _TableRecord,
)
from omniscribe.core.processors.table_fallback import TableFallbackProcessor

__all__ = [
    "LOCAL_DOCUMENT_PROCESSOR_NAMES",
    "DocumentProcessor",
    "DocumentProcessorFactory",
    "DocumentProcessorRegistry",
    "LayoutEnrichmentProcessor",
    "ProcessorContract",
    "QualityAnalysisProcessor",
    "ReadingOrderProcessor",
    "SectionAnalysisProcessor",
    "StructureAnalysisProcessor",
    "TableExtractionProcessor",
    "TableFallbackProcessor",
    "_TableCellRecord",
    "_TableRecord",
    "_bbox_area",
    "_normalize_space",
    "_page_region",
    "_structure_kind",
    "build_document_processors",
    "run_document_processors",
]
