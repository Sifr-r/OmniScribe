"""Rich document artifact — store/load helpers for the stored ``DocumentResult``.

The OCR plugin keeps producing its legacy text artifact
(``{page_index: "\\n".join(lines)}``) because the Flutter client and
:func:`~omniscribe.plugins.documents.service.load_pages` depend on that exact
shape. Alongside it, every OCR run that produced a rich
:class:`~omniscribe.core.document.DocumentResult` now stores a second,
capability-bound artifact: the serialized document itself, geometry, block
kinds, sections, tables, and trust fields included.

The handle travels on the existing metadata / headers channel
(``document_artifact_id`` + ``document_artifact_token`` in
``JobOutcome.metadata``, the ``X-Document-Artifact-*`` response header pair on
the sync path), so no new route or schema field is required to *write* it.

Pure module on purpose: JSON only, no FastAPI imports, so both plugin services
can use it. The store interaction is expressed against the
:class:`~omniscribe.plugins.artifacts.ArtifactStore` protocol, which keeps
this testable with the in-memory store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from omniscribe.core.block_tree import DocumentTree
from omniscribe.core.document import (
    DOCUMENT_SERIALIZATION_VERSION,
    DocumentResult,
    DocumentSerializationError,
)
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.documents.service import DocumentsError, tree_from_document

#: Envelope discriminator written into every stored document artifact. A
#: payload without it is a different artifact (e.g. the legacy text artifact)
#: and must not be fed to :func:`decode_document_artifact`.
DOCUMENT_ARTIFACT_SCHEMA = "omniscribe.document_result"

#: Envelope version. Kept in lockstep with the ``to_dict``/``from_dict`` pair
#: in ``core.document``; a mismatch is a typed error, never a partial read.
DOCUMENT_ARTIFACT_VERSION = DOCUMENT_SERIALIZATION_VERSION

#: Stored content type. ``application/json`` keeps the existing generic JSON
#: artifact fetch route able to serve it unchanged.
DOCUMENT_ARTIFACT_CONTENT_TYPE = "application/json"

#: Metadata keys carrying the opaque handle in ``JobOutcome.metadata`` and in
#: the job record's merged ``request_meta``.
DOCUMENT_ARTIFACT_META_ID = "document_artifact_id"
DOCUMENT_ARTIFACT_META_TOKEN = "document_artifact_token"


class DocumentArtifactError(DocumentsError):
    """A stored document artifact is unreadable or structurally invalid.

    Surfaces as a 500: the reference came from our own metadata, so a corrupt
    body is a store/serialization defect rather than a client mistake, and it
    must never degrade into a silently empty export tree.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(500, "artifact_corrupt", detail)


def encode_document_artifact(document: DocumentResult) -> bytes:
    """Serialize ``document`` into the versioned artifact envelope.

    Raises :class:`DocumentArtifactError` when a value in the document cannot
    be represented in JSON, so a lossy write is impossible.
    """
    try:
        envelope = {
            "schema": DOCUMENT_ARTIFACT_SCHEMA,
            "version": DOCUMENT_ARTIFACT_VERSION,
            "document": document.to_dict(),
        }
        return json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    except DocumentSerializationError as exc:
        raise DocumentArtifactError(
            f"Document result is not serializable: {exc}"
        ) from exc
    except (TypeError, ValueError) as exc:
        raise DocumentArtifactError(
            f"Document result is not serializable: {exc}"
        ) from exc


def decode_document_artifact(blob: bytes) -> DocumentResult:
    """Restore a :class:`DocumentResult` from a stored artifact body.

    Raises :class:`DocumentArtifactError` for non-JSON bytes, a foreign
    envelope, or a structurally broken document payload.
    """
    try:
        envelope = json.loads(blob)
    except (ValueError, UnicodeDecodeError) as exc:
        raise DocumentArtifactError(
            f"Document artifact is not valid JSON: {exc}"
        ) from exc
    if not isinstance(envelope, Mapping):
        raise DocumentArtifactError(
            f"Document artifact must be a JSON object, got {type(envelope).__name__}"
        )
    schema = envelope.get("schema")
    if schema != DOCUMENT_ARTIFACT_SCHEMA:
        raise DocumentArtifactError(f"Unexpected document artifact schema: {schema!r}")
    version = envelope.get("version")
    if version != DOCUMENT_ARTIFACT_VERSION:
        raise DocumentArtifactError(
            f"Unsupported document artifact version: {version!r} "
            f"(this build reads version {DOCUMENT_ARTIFACT_VERSION})"
        )
    try:
        return DocumentResult.from_dict(envelope.get("document", {}))
    except DocumentSerializationError as exc:
        raise DocumentArtifactError(
            f"Document artifact payload is invalid: {exc}"
        ) from exc


async def load_document_result(
    store: ArtifactStore, artifact_id: str, token: str
) -> DocumentResult | None:
    """Return the stored rich document, or ``None`` when only a text artifact exists.

    ``None`` means "not stored" (old job, older client) and is the caller's
    cue to fall back to the text-artifact heuristic path. A stored but corrupt
    body raises :class:`DocumentArtifactError` instead.
    """
    blob = await store.get(artifact_id, token)
    if blob is None:
        return None
    return decode_document_artifact(blob.blob)


async def load_document_tree(
    store: ArtifactStore, artifact_id: str, token: str
) -> DocumentTree | None:
    """Return the structural tree carried by the stored rich document."""
    document = await load_document_result(store, artifact_id, token)
    if document is None:
        return None
    return tree_from_document(document)


def document_artifact_ref(metadata: Mapping[str, Any]) -> tuple[str, str] | None:
    """Extract the ``(artifact_id, token)`` handle from a metadata mapping.

    Accepts a job record's ``request_meta``, a ``JobOutcome.metadata`` dict, or
    a header mapping that uses the artifact-id keys. Returns ``None`` when
    either half is missing, i.e. the job predates the rich document artifact.
    """
    artifact_id = metadata.get(DOCUMENT_ARTIFACT_META_ID)
    token = metadata.get(DOCUMENT_ARTIFACT_META_TOKEN)
    if (
        isinstance(artifact_id, str)
        and artifact_id
        and isinstance(token, str)
        and token
    ):
        return artifact_id, token
    return None


__all__ = [
    "DOCUMENT_ARTIFACT_CONTENT_TYPE",
    "DOCUMENT_ARTIFACT_META_ID",
    "DOCUMENT_ARTIFACT_META_TOKEN",
    "DOCUMENT_ARTIFACT_SCHEMA",
    "DOCUMENT_ARTIFACT_VERSION",
    "DocumentArtifactError",
    "decode_document_artifact",
    "document_artifact_ref",
    "encode_document_artifact",
    "load_document_result",
    "load_document_tree",
]
