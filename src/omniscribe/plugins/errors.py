"""Shared plugin-level error types.

The translate / transcribe / glossary plugins each raised a byte-identical
error class carrying the stable ``{status_code, error, detail}`` envelope
the route layer maps onto JSON responses (see ``_envelope`` in the route
factories). The wire contract lives here once; each plugin keeps a
distinct subclass so its routes can keep catching their own name.
"""

from __future__ import annotations


class PluginError(Exception):
    """User-facing plugin error carrying the envelope wire fields."""

    def __init__(self, status_code: int, error: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.error = error
        self.detail = detail


__all__ = ["PluginError"]
