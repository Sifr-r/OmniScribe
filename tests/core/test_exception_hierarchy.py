"""Tests for the OmniScribe domain exception hierarchy (audit C-10).

The hierarchy is the documented contract for "expected pipeline
failure"; these tests pin down the contract so future refactors don't
inadvertently break exception chaining or lose the ``details`` payload.
"""

from __future__ import annotations

import pytest

from omniscribe.core.errors import (
    AlignmentError,
    ArtifactAccessDeniedError,
    ArtifactError,
    ArtifactNotFoundError,
    CancellationError,
    ConfigError,
    DetectionError,
    EmbedError,
    GroundingError,
    HTTPFetchError,
    InvalidArtifactReferenceError,
    OCRError,
    OmniScribeError,
    PipelineError,
    PostprocessError,
    ResourceError,
    SSRFBlockedError,
    TranslationUnavailableError,
    redact_details,
    redact_exception,
    sanitize_job_error,
)


def test_root_class_is_not_a_builtin() -> None:
    """``OmniScribeError`` does NOT subclass any built-in.

    This is intentional: it prevents accidentally widening existing
    ``except ValueError`` clauses across the codebase. A test is the
    cheapest way to lock the contract down.
    """
    assert not issubclass(OmniScribeError, ValueError)
    assert not issubclass(OmniScribeError, RuntimeError)
    assert OmniScribeError.__mro__ == (
        OmniScribeError,
        Exception,
        BaseException,
        object,
    )


def test_message_round_trips() -> None:
    exc = OCRError("page 3 failed")
    assert str(exc) == "page 3 failed"
    assert exc.message == "page 3 failed"


def test_details_appear_in_str() -> None:
    exc = OCRError("page failed", details={"page": 3, "provider": "lm-studio"})
    rendered = str(exc)
    assert "page failed" in rendered
    assert "page=3" in rendered
    assert "provider='lm-studio'" in rendered


def test_details_are_sorted_in_str_for_log_dedup() -> None:
    """Same details in any insertion order must render identically."""
    a = OCRError("x", details={"a": 1, "b": 2, "c": 3})
    b = OCRError("x", details={"c": 3, "a": 1, "b": 2})
    assert str(a) == str(b)


def test_repr_is_deterministic_and_includes_type() -> None:
    exc = OCRError("boom", details={"page": 7})
    rendered = repr(exc)
    # ``repr`` round-trips through :func:`eval` for diagnostics.
    assert rendered.startswith("OCRError(")
    assert "'boom'" in rendered
    assert "details=" in rendered
    assert "page" in rendered
    assert "7" in rendered


def test_with_detail_returns_new_instance_not_mutate() -> None:
    original = OCRError("base", details={"page": 1})
    enriched = original.with_detail("job_id", "abc-123")
    # The original is untouched.
    assert "job_id" not in original.details
    assert original.details == {"page": 1}
    # The new instance carries both keys.
    assert enriched.details == {"page": 1, "job_id": "abc-123"}


def test_with_detail_preserves_cause() -> None:
    """``with_detail`` propagates ``__cause__`` for ``raise ... from`` chains."""

    class _CauseError(OmniScribeError):
        pass

    cause = _CauseError("root")
    wrapped = OCRError("wrapper")
    wrapped.__cause__ = cause
    enriched = wrapped.with_detail("page", 5)
    assert enriched.__cause__ is cause


def test_details_dict_is_copied_not_aliased() -> None:
    """Mutating the original dict after construction must not affect the exc."""
    payload = {"page": 1}
    exc = OCRError("x", details=payload)
    payload["page"] = 999
    assert exc.details == {"page": 1}


@pytest.mark.parametrize(
    "cls",
    [
        ConfigError,
        PipelineError,
        OCRError,
        AlignmentError,
        DetectionError,
        GroundingError,
        EmbedError,
        PostprocessError,
        ArtifactError,
        ArtifactNotFoundError,
        ArtifactAccessDeniedError,
        InvalidArtifactReferenceError,
        ResourceError,
        HTTPFetchError,
        SSRFBlockedError,
        TranslationUnavailableError,
        CancellationError,
    ],
)
def test_every_subclass_inherits_from_root(cls: type[OmniScribeError]) -> None:
    """Every public subclass is catchable as ``OmniScribeError``."""
    assert issubclass(cls, OmniScribeError)
    instance = cls("test")
    assert isinstance(instance, OmniScribeError)


def test_pipeline_subclasses_share_pipeline_base() -> None:
    """``except PipelineError`` catches every engine-level failure."""
    for cls in (
        OCRError,
        AlignmentError,
        DetectionError,
        GroundingError,
        EmbedError,
        PostprocessError,
    ):
        assert issubclass(cls, PipelineError)


def test_artifact_subclasses_share_artifact_base() -> None:
    for cls in (
        ArtifactNotFoundError,
        ArtifactAccessDeniedError,
        InvalidArtifactReferenceError,
    ):
        assert issubclass(cls, ArtifactError)


def test_resource_subclasses_share_resource_base() -> None:
    for cls in (HTTPFetchError, SSRFBlockedError, TranslationUnavailableError):
        assert issubclass(cls, ResourceError)


def test_artifact_subclasses_inherit_valueerror_for_router_compat() -> None:
    """Lock the contract that the artifact subclasses remain
    ``ValueError`` subclasses so any router-style ``except (ValueError,)``
    block keeps working (the historical reference was the
    pre-rebuild ``omniscribe.api.routers.artifacts`` catch-block;
    that module was removed in the API rebuild but the
    inheritance contract is still useful for any future router
    that wants the same shape).
    """
    assert issubclass(ArtifactNotFoundError, ValueError)
    assert issubclass(InvalidArtifactReferenceError, ValueError)


def test_http_fetch_error_records_status_and_url() -> None:
    exc = HTTPFetchError("upstream 5xx", status_code=503, url="https://x/y")
    assert exc.status_code == 503
    assert exc.url == "https://x/y"
    assert exc.details["status_code"] == 503
    assert exc.details["url"] == "https://x/y"


def test_http_fetch_error_status_and_url_default_to_none() -> None:
    exc = HTTPFetchError("network reset")
    assert exc.status_code is None
    assert exc.url is None


def test_raise_from_chaining_works() -> None:
    """``raise NewError(...) from exc`` populates ``__cause__``."""
    try:
        try:
            raise ValueError("original")
        except ValueError as inner:
            raise OCRError("wrapped") from inner
    except OCRError as exc:
        assert exc.__cause__ is not None
        assert isinstance(exc.__cause__, ValueError)


def test_redact_details_masks_known_secret_keys() -> None:
    """``redact_details`` masks password / token / api_key recursively."""
    exc = OCRError(
        "auth failed",
        details={
            "password": "hunter2",
            "api_key": "sk-abc",
            "token": "abc",
            "page": 3,
            "headers": {"Authorization": "Bearer xyz"},
        },
    )
    cleaned = redact_details(exc)
    assert cleaned.details["password"] == "***"
    assert cleaned.details["api_key"] == "***"
    assert cleaned.details["token"] == "***"
    assert cleaned.details["page"] == 3  # not redacted
    assert (
        cleaned.details["headers"]["Authorization"] == "Bearer xyz"
    )  # key not in denylist
    # Original is untouched.
    assert exc.details["password"] == "hunter2"


def test_redact_details_returns_same_instance_when_no_details() -> None:
    exc = OCRError("nothing")
    assert redact_details(exc) is exc


def test_redact_details_does_not_widen_exception_type() -> None:
    """``redact_details`` must preserve the exact subclass."""
    exc = AlignmentError("alignment failed", details={"page": 7})
    cleaned = redact_details(exc)
    assert type(cleaned) is AlignmentError


def test_can_be_raised_and_caught_as_root() -> None:
    """Smoke test: ``except OmniScribeError`` catches a leaf class."""
    with pytest.raises(OmniScribeError) as exc_info:
        raise OCRError("bad page", details={"page": 1})
    assert isinstance(exc_info.value, OCRError)
    assert exc_info.value.details["page"] == 1


def test_can_be_caught_by_mid_hierarchy_level() -> None:
    """``except PipelineError`` catches OCRError but NOT ArtifactError."""
    with pytest.raises(PipelineError):
        raise OCRError("x")
    with pytest.raises(ArtifactError):
        raise ArtifactNotFoundError("y")


def test_details_constructor_argument_is_optional() -> None:
    """``OmniScribeError("msg")`` (no details) does not raise."""
    exc = OCRError("just a message")
    assert exc.details == {}
    assert str(exc) == "just a message"


def test_empty_details_dict_treated_as_no_details() -> None:
    """``OmniScribeError("msg", details={})`` renders without the parens."""
    exc = OCRError("msg", details={})
    assert str(exc) == "msg"
    assert exc.details == {}


def test_sanitize_job_error_core() -> None:
    assert sanitize_job_error(None) is None
    assert sanitize_job_error("") == ""
    assert sanitize_job_error("Standard error message") == "Standard error message"
    assert (
        sanitize_job_error('Traceback:\n  File "x.py", line 1\nZeroDivisionError')
        == "An internal processing error occurred."
    )
    assert (
        sanitize_job_error("sqlite3.OperationalError: disk I/O error")
        == "A storage error occurred."
    )
    assert (
        sanitize_job_error("Failed with token=secret_val_12345")
        == "Failed with token=[redacted]"
    )
    assert sanitize_job_error("Failed at /var/log/secret.log") == "Failed at [path]"


def test_redact_exception() -> None:
    # Empty message uses class name
    assert redact_exception(RuntimeError()) == "RuntimeError"
    assert redact_exception(ValueError("")) == "ValueError"

    # Standard exception message
    assert redact_exception(ValueError("Invalid page index")) == "Invalid page index"

    # Traceback in exception
    assert (
        redact_exception(
            Exception('Traceback (most recent call last):\n  File "a.py", line 1')
        )
        == "An internal processing error occurred."
    )

    # Database exception
    assert (
        redact_exception(Exception("sqlite3.IntegrityError: constraint failed"))
        == "A storage error occurred."
    )

    # Secret redaction
    assert (
        redact_exception(Exception("Failed with api_key: secret-1234"))
        == "Failed with api_key: [redacted]"
    )

    # File path scrubbing
    assert (
        redact_exception(Exception(r"Cannot read C:\Windows\System32\drivers.sys"))
        == "Cannot read [path]"
    )
