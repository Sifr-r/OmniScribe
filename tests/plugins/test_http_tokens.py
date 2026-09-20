"""Unit tests for extract_token helper in omniscribe.plugins._http."""

from __future__ import annotations

from omniscribe.plugins._http import extract_token


class TestExtractToken:
    """Test suite for extract_token capability token extraction."""

    def test_extract_from_token_parameter(self) -> None:
        assert extract_token(token="  my-token  ") == "my-token"

    def test_extract_from_x_job_token(self) -> None:
        assert extract_token(x_job_token="  job-token-123  ") == "job-token-123"

    def test_extract_from_x_artifact_token(self) -> None:
        assert extract_token(x_artifact_token="  art-token-456  ") == "art-token-456"

    def test_extract_from_bearer_authorization(self) -> None:
        assert extract_token(authorization="Bearer   secret-token  ") == "secret-token"

    def test_extract_from_raw_authorization(self) -> None:
        assert (
            extract_token(authorization="  custom-auth-header  ")
            == "custom-auth-header"
        )

    def test_precedence_token_over_all(self) -> None:
        assert (
            extract_token(
                token="winner-token",
                x_job_token="loser-job",
                x_artifact_token="loser-art",
                authorization="Bearer loser-bearer",
            )
            == "winner-token"
        )

    def test_precedence_job_token_over_artifact_and_bearer(self) -> None:
        assert (
            extract_token(
                token="",
                x_job_token="winner-job",
                x_artifact_token="loser-art",
                authorization="Bearer loser-bearer",
            )
            == "winner-job"
        )

    def test_precedence_artifact_token_over_bearer(self) -> None:
        assert (
            extract_token(
                token="   ",
                x_job_token=None,
                x_artifact_token="winner-art",
                authorization="Bearer loser-bearer",
            )
            == "winner-art"
        )

    def test_empty_or_none_returns_none(self) -> None:
        assert extract_token() is None
        assert (
            extract_token(
                token="", authorization="", x_artifact_token="", x_job_token=""
            )
            is None
        )
        assert (
            extract_token(token="   ", authorization="   ", x_artifact_token="   ")
            is None
        )
