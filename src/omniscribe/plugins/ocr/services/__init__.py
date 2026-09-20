"""Sub-modules extracted from ``plugins/ocr/service.py``.

Phase 3.8 (4.8, 2026-09-05): the previous ``service.py`` mixed five
concerns in one ~890-LOC file. The three extracted modules are:

- :mod:`omniscribe.plugins.ocr.services.error_sanitization` — redacts
  internal details from job error messages before they reach the
  client.
- :mod:`omniscribe.plugins.ocr.services.content_sniff` — infers the
  file extension for an uploaded file from its filename and content
  type.
- :mod:`omniscribe.plugins.ocr.services.config_seeding` — seeds the
  ``/api/config`` in-memory store from ``RuntimeSettings`` at boot.

Each module exposes a small public surface (one or two functions /
constants) and keeps its implementation details private. The service
module imports only the public surface; tests and plugin routes can
do the same.
"""

from __future__ import annotations

from .config_seeding import CONFIG_KEY_SET, seed_config
from .content_sniff import guess_suffix
from .error_sanitization import redact_exception, sanitize_job_error

__all__ = [
    "CONFIG_KEY_SET",
    "guess_suffix",
    "redact_exception",
    "sanitize_job_error",
    "seed_config",
]
