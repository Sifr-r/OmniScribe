import argparse
import asyncio
import io
import json

import docx
import pymupdf as fitz
from fastapi.testclient import TestClient

from omniscribe.core.ocr.exceptions import LLMBalanceError
from omniscribe.plugins.artifacts import ArtifactStore
from omniscribe.plugins.jobs import JobRecord
from omniscribe.plugins.ocr.plugin import OCRService
from omniscribe.plugins.progress import ProgressService
from omniscribe.plugins.state_backend_types import StateBackend
from omniscribe.server import create_app


def run_async(coro):
    return asyncio.run(coro)


def main():
    parser = argparse.ArgumentParser(
        description="Run live non-mocked functional validation across OmniScribe services."
    )
    parser.parse_args()

    print("=" * 60)
    print("STARTING COMPREHENSIVE LIVE FUNCTIONAL VALIDATION")
    print("=" * 60)

    app = create_app()

    # Mount a route to verify LLMBalanceError propagation at HTTP boundary
    @app.get("/test-live-llm-balance")
    async def _fail_balance():
        raise LLMBalanceError("Provider upstream 402: insufficient balance (1008)")

    with TestClient(app) as client:
        # 1. Health & Readiness
        print("[CHECK 1] Health & Readiness Endpoints...")
        res = client.get("/api/health")
        assert res.status_code == 200, f"Expected 200, got {res.status_code}"
        assert res.json() == {"status": "ok"}, f"Unexpected: {res.json()}"

        res = client.get("/api/healthz")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

        res = client.get("/ready")
        assert res.status_code == 200
        assert res.json() == {"status": "ready"}

        res = client.get("/readyz")
        assert res.status_code == 200
        assert res.json() == {"status": "ready"}
        print("  -> Health & Readiness OK")

        # 2. Provider Discovery & Config
        print("[CHECK 2] Providers and Configuration...")
        res = client.get("/api/providers")
        assert res.status_code == 200
        providers_data = res.json()
        assert "providers" in providers_data or isinstance(providers_data, list)

        res = client.get("/api/config")
        assert res.status_code == 200
        config_data = res.json()
        assert (
            "model" in config_data
            or "pipeline" in config_data
            or isinstance(config_data, dict)
        )
        print("  -> Providers & Config OK")

        # 3. Transcription endpoints
        print("[CHECK 3] Transcription Service Endpoints...")
        res = client.get("/api/config/transcription")
        assert res.status_code == 200
        assert isinstance(res.json(), dict)

        res = client.get("/api/models/transcription")
        assert res.status_code == 200
        models_data = res.json()
        assert "models" in models_data or isinstance(models_data, list)
        print("  -> Transcription OK")

        # 4. Glossary endpoints
        print("[CHECK 4] Glossary Service Endpoints...")
        res = client.get("/api/glossary/sources")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        res = client.get("/api/glossary/library")
        assert res.status_code == 200
        assert isinstance(res.json(), list)
        print("  -> Glossary OK")

        # 5. Document Export - DOCX with real rich markdown
        print("[CHECK 5] Document Export - Rich DOCX...")
        rich_md = """# OmniScribe Architectural Verification

This is a test document validating real DOCX generation with rich text formatting.

## Performance Metrics

| Component | Target (ms) | Measured (ms) | Status |
| :--- | :--- | :--- | :--- |
| Layout Detection | 150 | 92 | Pass |
| Text OCR | 200 | 114 | Pass |
| Alignment | 80 | 38 | Pass |

- Item 1: First list element
- Item 2: Second list element
"""
        res = client.post("/api/export/docx", json={"text": rich_md})
        assert res.status_code == 200
        assert res.content[:4] == b"PK\x03\x04", (
            "Output is not a valid zip/docx archive"
        )

        # Verify valid Word document structure
        doc = docx.Document(io.BytesIO(res.content))
        headings = [
            p.text for p in doc.paragraphs if p.style.name.startswith("Heading")
        ]
        assert "OmniScribe Architectural Verification" in headings or any(
            "Verification" in h for h in headings
        )
        assert len(doc.tables) == 1, "Table was not created in docx"
        assert len(doc.tables[0].rows) == 4, "Table does not have 4 rows"
        print("  -> Rich DOCX Export OK (Table & Headings verified)")

        # 6. Document Export - DOCX with Whitespace / Empty Fallback
        print("[CHECK 6] Document Export - Empty DOCX Placeholder Guard...")
        for empty_payload in ["", "   ", "\n\n\t  \n"]:
            res = client.post("/api/export/docx", json={"text": empty_payload})
            assert res.status_code == 200
            doc = docx.Document(io.BytesIO(res.content))
            full_text = " ".join(p.text for p in doc.paragraphs)
            assert "No text recognized" in full_text, (
                f"Placeholder text missing for {empty_payload!r}"
            )
        print("  -> Empty DOCX Placeholder Guard OK")

        # Injected services from app context
        context = app.state.context
        artifacts: ArtifactStore = context.inject(ArtifactStore)
        ocr_service: OCRService = context.inject(OCRService)
        backend: StateBackend = context.inject(StateBackend)
        progress_service: ProgressService = context.inject(ProgressService)

        # 7. Document Export - Chunks
        print("[CHECK 7] Document Export - Chunks...")
        long_text = (
            "Executive Summary\n\n"
            + "This is a detailed operational report verifying that OmniScribe functions end to end without error. "
            * 10
        )
        text_payload = json.dumps({"0": long_text}).encode("utf-8")
        chunk_art = run_async(
            artifacts.put(
                text_payload,
                content_type="application/json",
                owner_job_id="job-chunks-test",
            )
        )
        res = client.post(
            "/api/export/chunks",
            json={
                "text_artifact_id": chunk_art.id,
                "text_artifact_token": chunk_art.token,
                "max_chars": 300,
                "overlap_chars": 50,
                "min_chars": 20,
            },
        )
        assert res.status_code == 200, (
            f"Expected 200, got {res.status_code}: {res.text}"
        )
        chunks_data = res.json()
        assert "chunks" in chunks_data
        assert len(chunks_data["chunks"]) >= 1, f"Expected chunks, got: {chunks_data}"
        print(
            f"  -> Document Chunks OK ({len(chunks_data['chunks'])} chunks generated)"
        )

        # 8. Error Envelopes
        print("[CHECK 8] Error Envelope Standards (404, 402, 422)...")
        res = client.get("/api/process/status/non-existent-job-uuid-12345")
        assert res.status_code == 404
        assert res.json().get("error") == "not_found"
        assert "detail" in res.json()

        res = client.get("/test-live-llm-balance")
        assert res.status_code == 402
        assert res.json().get("error") == "payment_required"
        assert "insufficient balance" in res.json().get("detail")
        print("  -> Error Envelopes OK (404 and 402 verified)")

        # 9. Injected Services: Preview Rendering with Fallback
        print("[CHECK 9] OCR Preview Rendering with Result Artifact Fallback...")
        # Create a sample 1-page PDF using PyMuPDF
        pdf_doc = fitz.open()
        page = pdf_doc.new_page(width=300, height=400)
        page.insert_text((50, 50), "OmniScribe Live Verification", fontsize=14)
        pdf_bytes = pdf_doc.tobytes()
        pdf_doc.close()

        # Store this PDF as an artifact
        art_handle = run_async(
            artifacts.put(
                pdf_bytes,
                content_type="application/pdf",
                owner_job_id="job-fallback-test",
            )
        )

        # Create a completed job record whose input_path is non-existent
        job_rec = JobRecord(
            job_id="job-fallback-test",
            status="complete",
            input_path="/non/existent/path/document.pdf",
            result_artifact_id=art_handle.id,
            result_artifact_token=art_handle.token,
        )
        run_async(backend.upsert_job(job_rec))

        # Request page preview - should fall back to result artifact
        preview_png = run_async(ocr_service.get_page_preview("job-fallback-test", 0))
        assert preview_png is not None, "Preview PNG was None"
        assert preview_png[:8] == b"\x89PNG\r\n\x1a\n", (
            "Preview output is not a valid PNG image"
        )
        print("  -> Preview Fallback to Artifact OK (Valid PNG output verified)")

        # 10. WebSocket Connection & Communication
        print("[CHECK 10] WebSocket Channel LifeCycle...")
        handle = run_async(progress_service.open_channel())
        channel_id = handle.channel_id
        token = handle.session_token

        with client.websocket_connect(f"/ws/{channel_id}?token={token}") as ws:
            conn_frame = json.loads(ws.receive_text())
            assert conn_frame.get("type") == "connected"
            assert conn_frame.get("channel_id") == channel_id

            # Broadcast an event through progress service
            run_async(
                progress_service.broadcast(
                    channel_id, {"type": "progress", "percent": 50, "stage": "OCR"}
                )
            )
            received = json.loads(ws.receive_text())
            assert received.get("type") == "progress"
            assert received.get("percent") == 50
        print("  -> WebSocket Progress OK")

    print("=" * 60)
    print("ALL 10 FUNCTIONAL VERIFICATION CHECKS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    main()
