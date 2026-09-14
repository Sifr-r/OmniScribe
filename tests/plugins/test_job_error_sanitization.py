"""Tests for Job Error Sanitization (Wave 6 Phase C Follow-up 1).

Phase 3.8 (4.8, 2026-09-05): ``sanitize_job_error`` was extracted to
:mod:`omniscribe.plugins.ocr.services.error_sanitization` and renamed
``sanitize_job_error``.
"""

from __future__ import annotations

from omniscribe.plugins.ocr.service import OCRServiceImpl
from omniscribe.plugins.ocr.services import sanitize_job_error
from omniscribe.plugins.state_backend import JobRecord


def testsanitize_job_error_none_and_empty() -> None:
    assert sanitize_job_error(None) is None
    assert sanitize_job_error("") == ""


def testsanitize_job_error_normal_messages() -> None:
    assert sanitize_job_error("Job cancelled.") == "Job cancelled."
    assert (
        sanitize_job_error("VLM request timed out after 30s")
        == "VLM request timed out after 30s"
    )
    assert (
        sanitize_job_error("Rate limit reached for LLM provider")
        == "Rate limit reached for LLM provider"
    )
    assert (
        sanitize_job_error("Model error: empty response returned")
        == "Model error: empty response returned"
    )
    assert (
        sanitize_job_error("Validation error: document has no pages")
        == "Validation error: document has no pages"
    )


def testsanitize_job_error_file_paths() -> None:
    assert (
        sanitize_job_error(
            r"Error reading file D:\OmniScribe\temp\secret_doc.pdf: corrupt header"
        )
        == "Error reading file [path]: corrupt header"
    )
    assert (
        sanitize_job_error("Error reading file /tmp/secret_doc.pdf: corrupt header")
        == "Error reading file [path]: corrupt header"
    )
    assert (
        sanitize_job_error("Failed to write to /var/log/omniscribe/out.txt.")
        == "Failed to write to [path]."
    )
    assert (
        sanitize_job_error("Copied from /home/user/in.pdf to /tmp/out.pdf")
        == "Copied from [path] to [path]"
    )


def testsanitize_job_error_raw_python_traceback() -> None:
    traceback_err = (
        "Traceback (most recent call last):\n"
        '  File "D:\\OmniScribe\\service.py", line 142, in run\n'
        '    raise RuntimeError("worker crashed")\n'
        "RuntimeError: worker crashed"
    )
    assert sanitize_job_error(traceback_err) == "An internal processing error occurred."

    frame_only = 'File "/app/omniscribe/engine.py", line 55, in execute'
    assert sanitize_job_error(frame_only) == "An internal processing error occurred."


def testsanitize_job_error_database_errors() -> None:
    assert (
        sanitize_job_error("sqlite3.OperationalError: no such table: artifacts")
        == "A storage error occurred."
    )
    assert (
        sanitize_job_error("sqlite3.IntegrityError: UNIQUE constraint failed")
        == "A storage error occurred."
    )
    assert (
        sanitize_job_error("OperationalError: database is locked")
        == "A storage error occurred."
    )
    assert (
        sanitize_job_error('syntax error near "SELECT * FROM"')
        == "A storage error occurred."
    )
    assert (
        sanitize_job_error("IntegrityError: foreign key violation")
        == "A storage error occurred."
    )


def testsanitize_job_error_secrets_and_tokens() -> None:
    assert (
        sanitize_job_error("Failed with token=secret_token_12345")
        == "Failed with token=[redacted]"
    )
    assert (
        sanitize_job_error("Failed with api_key: my-secret-api-key")
        == "Failed with api_key: [redacted]"
    )
    assert (
        sanitize_job_error("Authorization: Bearer my_bearer_token_abc")
        == "Authorization: Bearer [redacted]"
    )
    assert (
        sanitize_job_error("OpenAI error with sk-12345678901234567890abc")
        == "OpenAI error with [redacted]"
    )


def test_status_response_outputs_sanitized_error() -> None:
    service = object.__new__(OCRServiceImpl)
    assert isinstance(service, OCRServiceImpl)

    # Database error -> storage error
    db_record = JobRecord(
        job_id="job-db-err",
        status="error",
        error="sqlite3.OperationalError: no such table: artifacts",
    )
    status_resp = service._status_response(db_record)
    assert status_resp.error == "A storage error occurred."

    # Traceback -> internal processing error
    tb_record = JobRecord(
        job_id="job-tb-err",
        status="error",
        error='Traceback (most recent call last):\n  File "worker.py", line 1\nZeroDivisionError',
    )
    status_resp = service._status_response(tb_record)
    assert status_resp.error == "An internal processing error occurred."

    # File path -> path redacted
    path_record = JobRecord(
        job_id="job-path-err",
        status="error",
        error=r"Error reading file D:\OmniScribe\temp\secret_doc.pdf: corrupt header",
    )
    status_resp = service._status_response(path_record)
    assert status_resp.error == "Error reading file [path]: corrupt header"

    # Clean error -> preserved
    clean_record = JobRecord(
        job_id="job-clean-err",
        status="error",
        error="VLM request timed out after 30s",
    )
    status_resp = service._status_response(clean_record)
    assert status_resp.error == "VLM request timed out after 30s"

    # None error on success -> None
    ok_record = JobRecord(
        job_id="job-ok",
        status="complete",
        error=None,
    )
    status_resp = service._status_response(ok_record)
    assert status_resp.error is None

    # Cancelled with None error -> default "Job cancelled."
    cancelled_record = JobRecord(
        job_id="job-cancelled",
        status="cancelled",
        error=None,
    )
    status_resp = service._status_response(cancelled_record)
    assert status_resp.error == "Job cancelled."
