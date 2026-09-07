"""DocumentResult processor interfaces and registry.

Processors run after OCR/refinement/spellcheck and before output embedding.
They receive the mutable normalized document graph, so changes to block text,
order, and metadata are visible to later processors and to export surfaces that
read ``OCRPipeline.last_document_result``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy
from enum import Enum
from typing import Protocol

from omniscribe.core.document import DocumentBlock, DocumentResult

LOCAL_DOCUMENT_PROCESSOR_NAMES = (
    "reading_order",
    "quality_analysis",
    "structure_analysis",
    "section_analysis",
    "layout_enrichment",
    "table_extraction",
    "table_fallback",
)


class ProcessorContract(Enum):
    """Mutation contract that a document processor declares about itself.

    Declared as a class attribute (``contract``) on every processor. The
    :func:`run_document_processors` strict-mode gate checks the contract
    against observed mutations: a processor that drops blocks but
    declared ``ANNOTATE_ONLY`` raises ``ValueError`` instead of silently
    corrupting output. The gate is opt-in (pass ``strict=True``) so
    production pipelines keep paying zero overhead.
    """

    ANNOTATE_ONLY = "annotate_only"
    """Processor only attaches metadata; block count and order are preserved."""

    MAY_REORDER = "may_reorder"
    """Processor may reorder blocks but must not drop or duplicate them."""

    MAY_DELETE = "may_delete"
    """Processor may drop, merge, or otherwise remove blocks (e.g. table extraction)."""


class DocumentProcessor(Protocol):
    """Async transform contract for in-memory document handoff stages."""

    name: str
    contract: ProcessorContract

    async def process(self, document: DocumentResult) -> DocumentResult: ...


DocumentProcessorFactory = Callable[[], DocumentProcessor]


class DocumentProcessorRegistry:
    """Name-to-factory registry used by callers that expose processor choices.

    Factories should return fresh processor instances; processors may keep
    per-run state and the pipeline executes them sequentially.
    """

    def __init__(self) -> None:
        self._factories: dict[str, DocumentProcessorFactory] = {}

    @property
    def names(self) -> list[str]:
        return sorted(self._factories)

    def register(self, name: str, factory: DocumentProcessorFactory) -> None:
        key = name.strip()
        if not key:
            raise ValueError("Document processor name cannot be empty")
        if key in self._factories:
            raise ValueError(f"Document processor already registered: {key}")
        self._factories[key] = factory

    def create(self, name: str) -> DocumentProcessor:
        try:
            return self._factories[name]()
        except KeyError:
            raise KeyError(f"Unknown document processor: {name}") from None

    def create_many(self, names: Sequence[str]) -> list[DocumentProcessor]:
        return [self.create(name) for name in names]


def build_document_processors(names: Iterable[str]) -> tuple[DocumentProcessor, ...]:
    """Instantiate known local document processors by user-facing name."""
    from omniscribe.core.processors.layout import LayoutEnrichmentProcessor
    from omniscribe.core.processors.quality import QualityAnalysisProcessor
    from omniscribe.core.processors.reading_order import ReadingOrderProcessor
    from omniscribe.core.processors.section import SectionAnalysisProcessor
    from omniscribe.core.processors.structure import StructureAnalysisProcessor
    from omniscribe.core.processors.table import TableExtractionProcessor
    from omniscribe.core.processors.table_fallback import TableFallbackProcessor

    registry = DocumentProcessorRegistry()
    registry.register("reading_order", ReadingOrderProcessor)
    registry.register("quality_analysis", QualityAnalysisProcessor)
    registry.register("structure_analysis", StructureAnalysisProcessor)
    registry.register("section_analysis", SectionAnalysisProcessor)
    registry.register("layout_enrichment", LayoutEnrichmentProcessor)
    registry.register("table_extraction", TableExtractionProcessor)
    registry.register("table_fallback", TableFallbackProcessor)
    return tuple(registry.create(name) for name in names)


def _structure_kind(block: DocumentBlock) -> str:
    structure = block.metadata.get("structure")
    if isinstance(structure, dict):
        kind = structure.get("kind")
        if isinstance(kind, str):
            return kind
    return block.kind


def _normalize_space(text: str) -> str:
    return " ".join(text.split())


def _page_region(bbox: Sequence[float]) -> str:
    _x0, y0, _x1, y1 = bbox
    if y1 <= 0.16:
        return "header"
    if y0 >= 0.84:
        return "footer"
    return "body"


def _bbox_area(bbox: Sequence[float]) -> float:
    x0, y0, x1, y1 = bbox
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


async def run_document_processors(
    document: DocumentResult,
    processors: Sequence[DocumentProcessor],
    strict: bool = False,
) -> DocumentResult:
    """Run processors in order, passing each mutation to the next stage.

    When ``strict=True`` each processor is checked against its declared
    :class:`ProcessorContract`:

    - ``ANNOTATE_ONLY`` — block count and order must be unchanged.
    - ``MAY_REORDER`` — block count and (set of) text identities must be
      unchanged; block order may change.
    - ``MAY_DELETE`` — no contract check; processor may drop or merge
      blocks.

    A processor that violates its declared contract raises ``ValueError``
    instead of silently corrupting downstream output. ``strict=False``
    (the default) preserves zero-overhead production runs.
    """

    if document.tree is None:
        from omniscribe.core.block_tree import from_document_result

        document.tree = from_document_result(document)

    original_block_count = sum(len(page.blocks) for page in document.pages)
    original_texts: list[str] = [
        block.text for page in document.pages for block in page.blocks
    ]

    result = document
    for processor in processors:
        if strict:
            pre = deepcopy(result)
        result = await processor.process(result)
        if strict:
            _enforce_processor_contract(processor, pre, result)

    if strict:
        allows_deletion = any(
            getattr(p, "contract", None) == ProcessorContract.MAY_DELETE
            for p in processors
        )
        final_count = sum(len(page.blocks) for page in result.pages)
        final_texts = [block.text for page in result.pages for block in page.blocks]
        if not allows_deletion and final_count != original_block_count:
            raise ValueError(
                "run_document_processors(strict=True): aggregate block count "
                f"changed ({original_block_count} -> {final_count}); at least "
                "one processor with ANNOTATE_ONLY / MAY_REORDER contract "
                "violated its declaration"
            )
        if not allows_deletion and sorted(final_texts) != sorted(original_texts):
            # Block text identity changed — only MAY_DELETE processors are
            # allowed to alter text multisets. ANNOTATE_ONLY is caught by
            # the per-processor checks above.
            raise ValueError(
                "run_document_processors(strict=True): aggregate block text "
                "multiset changed while block count was preserved; at least "
                "one processor violated its declared contract"
            )
    return result


def _enforce_processor_contract(
    processor: DocumentProcessor,
    before: DocumentResult,
    after: DocumentResult,
) -> None:
    """Raise ``ValueError`` when a processor violates its declared contract."""
    contract = getattr(processor, "contract", None)
    if contract is None:
        # Processors without a contract declaration opt out of strict-mode
        # enforcement; the pipeline owner should add one when adding a new
        # processor type.
        return

    before_count = sum(len(page.blocks) for page in before.pages)
    after_count = sum(len(page.blocks) for page in after.pages)
    before_texts = [block.text for page in before.pages for block in page.blocks]
    after_texts = [block.text for page in after.pages for block in page.blocks]

    name = getattr(processor, "name", type(processor).__name__)

    if contract is ProcessorContract.ANNOTATE_ONLY:
        if after_count != before_count:
            raise ValueError(
                f"Processor {name!r} declares ANNOTATE_ONLY but changed block "
                f"count {before_count} -> {after_count}"
            )
        if after_texts != before_texts:
            raise ValueError(
                f"Processor {name!r} declares ANNOTATE_ONLY but reordered or "
                "rewrote block text"
            )
        return

    if contract is ProcessorContract.MAY_REORDER:
        if after_count != before_count:
            raise ValueError(
                f"Processor {name!r} declares MAY_REORDER but changed block "
                f"count {before_count} -> {after_count}"
            )
        if sorted(after_texts) != sorted(before_texts):
            raise ValueError(
                f"Processor {name!r} declares MAY_REORDER but dropped or "
                "duplicated block text identities"
            )
        return

    if contract is ProcessorContract.MAY_DELETE:
        return

    raise ValueError(
        f"Processor {name!r} declares unknown ProcessorContract value "
        f"{contract!r}; expected ANNOTATE_ONLY / MAY_REORDER / MAY_DELETE"
    )
