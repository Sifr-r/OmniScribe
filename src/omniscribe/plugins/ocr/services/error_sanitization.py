"""Job error sanitization for the OCR service.

Re-exports core error sanitization helpers from ``omniscribe.core.errors``
for backward compatibility.
"""

from __future__ import annotations

from omniscribe.core.errors import (
    redact_exception,
    sanitize_job_error,
)

__all__ = ["redact_exception", "sanitize_job_error"]
