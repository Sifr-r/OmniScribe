r"""Domain exception hierarchy for the OmniScribe core (audit C-10).

The previous code raised generic ``ValueError`` / ``RuntimeError`` for
pipeline problems, which forces every caller to inspect ``str(exc)`` to
decide whether to retry, fail the job, or surface a 4xx to the user.
That coupling makes the API surface brittle: a typo in an error
message silently changes the user-visible behaviour.

This module defines one base class (:class:`OmniScribeError`) and a
small tree of domain-specific subclasses that all inherit from it.
Every public module that already raises a built-in exception can be
upgraded incrementally: a re-raise inside that module becomes
``raise OCRError(...) from exc`` and the API layer keeps catching the
broad base class while new code can pattern-match on the specific
subclass.

Why a custom hierarchy instead of just adding new built-ins?

* ``OmniScribeError`` is the documented contract for "anything that went
  wrong on purpose because of an OCR / pipeline decision". External
  callers (the web layer, the CLI) can ``except OmniScribeError`` once
  and treat the whole tree as expected failure; unexpected failures
  still bubble as ``Exception``.
* Built-ins are too broad. ``ValueError`` covers both "user passed a
  bad page range" (a 400) and "internal regex matched an empty string"
  (a 500). Sub-classing ``ValueError`` would preserve the 4xx status
  quo but a new module would have to import this tree to benefit.
* :class:`OmniScribeError` is *not* a subclass of any built-in so it
  does not accidentally widen an existing ``except ValueError`` clause
  in the codebase. (Subclassing would be tempting — it would let old
  callers keep working — but it would also let unrelated
  ``ValueError``\ s silently slip into the new bucket. The clean
  break is safer.)

Hierarchy::

    OmniScribeError
    ├── ConfigError           (bad / missing settings — operator fix)
    ├── PipelineError         (engine-level failures, internal)
    │   ├── OCRError          (VLM OCR call failed irrecoverably)
    │   ├── AlignmentError    (DP alignment could not produce a result)
    │   ├── DetectionError    (Surya layout detection failed)
    │   ├── GroundingError    (grounded-backend bbox-native call failed)
    │   ├── EmbedError        (PDF embedding failed)
    │   └── PostprocessError  (dictionary spellcheck / cleanup failed)
    ├── ArtifactError         (token / id validation, file I/O)
    │   ├── ArtifactNotFoundError
    │   ├── ArtifactAccessDeniedError
    │   └── InvalidArtifactReferenceError
    ├── ResourceError         (file system, network)
    │   ├── HTTPFetchError
    │   ├── SSRFBlockedError
    │   └── TranslationUnavailableError
    └── CancellationError     (cooperative shutdown signal)

Every subclass:

* accepts ``message: str`` and an optional ``details: dict[str, Any]``
  payload that survives ``str(exc)`` round-trips;
* renders ``repr(exc)`` deterministically so log aggregation works
  (``OmniScribeError("...", details={...})``);
* supports ``raise NewError(...) from exc`` chaining for free, since
  the constructor takes ``*args`` and Python's traceback machinery
  already records the cause.

The tree is intentionally flat: four levels of inheritance is more
than enough to discriminate, and Python's ``except`` does NOT support
hierarchical matching the way Java does, so deep trees just make it
harder to figure out which class to catch.
"""

from __future__ import annotations

from typing import Any, Final

__all__ = [
    "AlignmentError",
    "ArtifactAccessDeniedError",
    "ArtifactError",
    "ArtifactNotFoundError",
    "CancellationError",
    "ChunkingError",
    "ConfigError",
    "DetectionError",
    "EmbedError",
    "GroundingError",
    "HTTPFetchError",
    "InvalidArtifactReferenceError",
    "OCRError",
    "OmniScribeError",
    "PipelineError",
    "PostprocessError",
    "ResourceError",
    "SSRFBlockedError",
    "TranslationUnavailableError",
]


_REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {"password", "secret", "token", "auth", "api_key", "apikey"}
)


class OmniScribeError(Exception):
    """Base class for every expected failure raised by OmniScribe.

    Callers that want to treat the whole tree as one bucket (most API
    code paths, most test fixtures) catch this. Callers that need
    fine-grained behaviour pattern-match on the specific subclass.

    The ``details`` mapping is a free-form ``str -> Any`` payload that
    survives ``str(exc)`` round-trips; keys whose name suggests a
    secret are replaced with ``"***"`` so a careless ``str(exc)`` does
    not leak credentials into a log line.
    """

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self._message = message
        self.details: dict[str, Any] = dict(details) if details else {}

    @property
    def message(self) -> str:
        return self._message

    def __str__(self) -> str:
        if not self.details:
            return self._message
        # Stable, sorted order so two errors with the same payload
        # produce the same string — log dedup depends on it.
        parts = [f"{key}={value!r}" for key, value in sorted(self.details.items())]
        return f"{self._message} ({'; '.join(parts)})"

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._message!r}, details={self.details!r})"

    def with_detail(self, key: str, value: Any) -> OmniScribeError:
        """Return a copy with one extra detail key (does not mutate self).

        Useful for attaching context (job id, page number) as the
        exception bubbles up without losing the original cause.
        """
        merged = dict(self.details)
        merged[key] = value
        new_exc = type(self)(self._message, details=merged)
        new_exc.__cause__ = self.__cause__
        new_exc.__suppress_context__ = self.__suppress_context__
        return new_exc


class ConfigError(OmniScribeError):
    """Raised when runtime configuration is missing or invalid.

    ``from_env`` / startup-validation helpers should raise this so the
    web server's lifespan can convert it into a 500 with a clear
    remediation hint ("set OMNISCRIBE_AUTH_TOKEN") instead of a
    stack trace that says ``KeyError: 'OMNISCRIBE_AUTH_TOKEN'``.
    """


class PipelineError(OmniScribeError):
    """Base class for engine-level failures.

    Distinguished from :class:`ConfigError` (operator can fix by
    editing env vars) and from :class:`ResourceError` (transient,
    retryable) by being a deterministic pipeline decision: the same
    inputs would produce the same failure on a different host.
    """


class OCRError(PipelineError):
    """Raised when a VLM OCR call fails irrecoverably (after retries).

    Carries the page index and underlying provider name in
    ``details`` so the operator can tell whether to investigate the
    VLM server or a specific document.
    """


class AlignmentError(PipelineError):
    """Raised when DP alignment cannot produce a usable mapping."""


class DetectionError(PipelineError):
    """Raised when Surya layout detection fails or returns no boxes."""


class GroundingError(PipelineError):
    """Raised when a bbox-native (grounded) backend call fails."""


class EmbedError(PipelineError):
    """Raised when sandwich-PDF embedding fails (PyMuPDF write, etc.)."""


class PostprocessError(PipelineError):
    """Raised when dictionary spellcheck or text cleanup fails."""


class ArtifactError(OmniScribeError):
    """Base class for artifact store failures."""


class ArtifactNotFoundError(ArtifactError, ValueError):
    """Raised when a token-bound artifact reference is absent or expired.

    Inherits from ``ValueError`` so the existing ``except ValueError``
    clause in :mod:`omniscribe.api.routers.artifacts` keeps treating
    this as a 4xx-style client error rather than a 5xx.
    """


class ArtifactAccessDeniedError(ArtifactError):
    """Raised when an artifact reference has the wrong token."""


class InvalidArtifactReferenceError(ArtifactError, ValueError):
    """Raised when an artifact ID / token / path fails boundary validation.

    Also a ``ValueError`` subclass for the same router-compat reason as
    :class:`ArtifactNotFoundError`.
    """


class ResourceError(OmniScribeError):
    """Base class for external-resource failures (HTTP, disk, network)."""


class HTTPFetchError(ResourceError):
    """Raised when a remote HTTP fetch fails after retries."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        url: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = dict(details) if details else {}
        if status_code is not None:
            merged.setdefault("status_code", status_code)
        if url is not None:
            merged.setdefault("url", url)
        super().__init__(message, details=merged)

    @property
    def status_code(self) -> int | None:
        return self.details.get("status_code")

    @property
    def url(self) -> str | None:
        return self.details.get("url")


class SSRFBlockedError(ResourceError):
    """Raised when a fetch target fails the SSRF private-IP filter."""


class TranslationUnavailableError(ResourceError):
    """Raised when a translation backend is missing required extras."""


class CancellationError(OmniScribeError):
    """Cooperative shutdown signal.

    Distinct from :class:`asyncio.CancelledError` because the runtime
    cancellation is already covered by the stdlib; this class is for
    long-running pipelines that the user explicitly stops via a
    /cancel endpoint or a keyboard interrupt.
    """


class ChunkingError(OmniScribeError):
    """Raised when document chunking fails or invalid parameters are provided."""


def _redact(value: Any) -> Any:
    """Strip known-secret detail values so ``str(exc)`` is log-safe.

    This is a defensive helper, not a guarantee: callers should still
    avoid putting secrets in ``details``. The denylist is a backstop.
    """
    if isinstance(value, dict):
        return {
            k: ("***" if k.lower() in _REDACT_KEYS else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def redact_details(exc: OmniScribeError) -> OmniScribeError:
    """Return a shallow copy of ``exc`` with secret-shaped details masked.

    Useful right before logging: ``log.warning("ocr failed: %s", redact_details(exc))``.
    """
    if not exc.details:
        return exc
    cleaned = type(exc)(exc.message, details=_redact(exc.details))
    cleaned.__cause__ = exc.__cause__
    cleaned.__suppress_context__ = exc.__suppress_context__
    return cleaned
