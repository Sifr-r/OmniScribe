"""Coverage push for the two security-adjacent glossary sources (audit P3-11).

``sql_table`` — SQL identifier handling and WHERE-clause parameterization.
``git_repo`` — remote fetch command construction, credential plumbing, and
path validation.

Both modules are optional-dependency tolerant: the SQL tests skip when
SQLAlchemy is absent (the ImportError path is covered separately) and the
git tests stub ``subprocess.run`` so no git binary or network is needed.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from omniscribe.core.glossary_sources import (
    FormatNotAvailableError,
    parse,
    parse_git_glossary,
    parse_sql_table,
)
from omniscribe.core.glossary_sources import git_repo as git_repo_mod


def _sqlalchemy_available() -> bool:
    try:
        import sqlalchemy  # noqa: F401
    except ImportError:
        return False
    return True


requires_sqlalchemy = pytest.mark.skipif(
    not _sqlalchemy_available(), reason="sqlalchemy not installed (glossary extra)"
)


# ---------------------------------------------------------------------------
# sql_table
# ---------------------------------------------------------------------------


def test_sql_missing_sqlalchemy_raises_format_not_available() -> None:
    if _sqlalchemy_available():
        pytest.skip("sqlalchemy installed; the ImportError path is unreachable")
    with pytest.raises(FormatNotAvailableError):
        parse_sql_table(
            dsn="sqlite://", source_table="terms", source_col="src", target_col="tgt"
        )


@pytest.fixture
def sql_db(tmp_path: Path) -> Path:
    from sqlalchemy import create_engine, text

    path = tmp_path / "glossary.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE terms (src TEXT, tgt TEXT, score INTEGER)"))
            conn.execute(
                text(
                    "INSERT INTO terms (src, tgt, score) VALUES "
                    "('Hello', 'Hola', 1), ('World', 'Mundo', 2), "
                    "('Acme Corp', 'Acme Sociedad', 3)"
                )
            )
    finally:
        engine.dispose()
    return path


@requires_sqlalchemy
class TestSqlTable:
    def test_reads_pairs(self, sql_db: Path) -> None:
        summary = parse_sql_table(
            dsn=f"sqlite:///{sql_db}",
            source_table="terms",
            source_col="src",
            target_col="tgt",
        )
        assert summary.format == "sql_table"
        pairs = {entry["source"]: entry["target"] for entry in summary.entries}
        assert pairs == {
            "Hello": "Hola",
            "World": "Mundo",
            "Acme Corp": "Acme Sociedad",
        }

    def test_dispatches_through_parse(self, sql_db: Path) -> None:
        summary = parse(
            format="sql_table",
            dsn=f"sqlite:///{sql_db}",
            source_table="terms",
            source_col="src",
            target_col="tgt",
        )
        assert summary.format == "sql_table"
        assert len(summary.entries) == 3

    def test_sqlite3_scheme_alias(self, sql_db: Path) -> None:
        summary = parse_sql_table(
            dsn=f"sqlite3:///{sql_db}",
            source_table="terms",
            source_col="src",
            target_col="tgt",
        )
        assert len(summary.entries) == 3

    def test_where_clause_is_parameterized(self, sql_db: Path) -> None:
        summary = parse_sql_table(
            dsn=f"sqlite:///{sql_db}",
            source_table="terms",
            source_col="src",
            target_col="tgt",
            where_clause="src = 'Hello'",
        )
        assert [entry["source"] for entry in summary.entries] == ["Hello"]

    def test_where_clause_numeric_and_like(self, sql_db: Path) -> None:
        summary = parse_sql_table(
            dsn=f"sqlite:///{sql_db}",
            source_table="terms",
            source_col="src",
            target_col="tgt",
            where_clause="score > 1 AND src LIKE 'W%'",
        )
        assert [entry["source"] for entry in summary.entries] == ["World"]

    @pytest.mark.parametrize(
        "evil_where",
        [
            "1=1 OR 1=1",
            "src = 'x'; DROP TABLE terms",
            "src IN (SELECT src FROM terms)",
            "src = 'x' UNION SELECT tgt, src, score FROM terms",
        ],
    )
    def test_where_clause_rejects_compound_predicates(
        self, sql_db: Path, evil_where: str
    ) -> None:
        with pytest.raises(ValueError, match="where_clause"):
            parse_sql_table(
                dsn=f"sqlite:///{sql_db}",
                source_table="terms",
                source_col="src",
                target_col="tgt",
                where_clause=evil_where,
            )

    @pytest.mark.parametrize(
        "field_kwargs",
        [
            {"source_table": "terms; DROP TABLE terms"},
            {"source_table": "terms WHERE 1=1 --"},
            {"source_col": "src FROM terms --"},
            {"target_col": "tgt) UNION SELECT 1 --"},
        ],
    )
    def test_identifier_injection_rejected(
        self, sql_db: Path, field_kwargs: dict[str, str]
    ) -> None:
        kwargs: dict[str, str] = {
            "dsn": f"sqlite:///{sql_db}",
            "source_table": "terms",
            "source_col": "src",
            "target_col": "tgt",
        }
        kwargs.update(field_kwargs)
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            parse_sql_table(**kwargs)

    def test_target_table_mismatch_rejected(self, sql_db: Path) -> None:
        with pytest.raises(ValueError, match="must match"):
            parse_sql_table(
                dsn=f"sqlite:///{sql_db}",
                source_table="terms",
                source_col="src",
                target_col="tgt",
                target_table="other_table",
            )

    def test_empty_dsn_rejected(self) -> None:
        with pytest.raises(ValueError, match="DSN is required"):
            parse_sql_table(
                dsn="   ", source_table="terms", source_col="src", target_col="tgt"
            )

    def test_empty_table_raises_no_valid_pairs(self, tmp_path: Path) -> None:
        from sqlalchemy import create_engine, text

        path = tmp_path / "empty.db"
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE terms (src TEXT, tgt TEXT)"))
        finally:
            engine.dispose()
        with pytest.raises(ValueError, match="no valid pairs"):
            parse_sql_table(
                dsn=f"sqlite:///{path}",
                source_table="terms",
                source_col="src",
                target_col="tgt",
            )

    def test_ssrf_blocked_dsn_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")
        with pytest.raises(ValueError, match="forbidden"):
            parse_sql_table(
                dsn="postgresql://user:pass@169.254.169.254:5432/mydb",
                source_table="terms",
                source_col="src",
                target_col="tgt",
            )


# ---------------------------------------------------------------------------
# git_repo
# ---------------------------------------------------------------------------


def _tar_with(path: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=path)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


@pytest.fixture
def git_env(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub the SSRF gate and capture the git command instead of running it."""
    monkeypatch.setattr(git_repo_mod, "_ssrf_blocked", lambda url: False)
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["timeout"] = kwargs.get("timeout")
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(
            command, 0, stdout=captured.get("stdout", b""), stderr=b""
        )

    monkeypatch.setattr(git_repo_mod.subprocess, "run", fake_run)
    return captured


class TestGitValidation:
    def test_empty_url_rejected(self) -> None:
        with pytest.raises(ValueError, match="URL is required"):
            parse_git_glossary(url="   ")

    def test_ssrf_blocked_url_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(git_repo_mod, "_ssrf_blocked", lambda url: True)
        with pytest.raises(ValueError, match="not allowed"):
            parse_git_glossary(url="http://169.254.169.254/repo.git")

    def test_empty_ref_rejected(self, git_env: dict) -> None:
        with pytest.raises(ValueError, match="ref"):
            parse_git_glossary(url="https://example.com/repo.git", ref="   ")

    @pytest.mark.parametrize("bad_timeout", [0, -5, 601])
    def test_timeout_bounds(self, git_env: dict, bad_timeout: int) -> None:
        with pytest.raises(ValueError, match="timeout_sec"):
            parse_git_glossary(
                url="https://example.com/repo.git", timeout_sec=bad_timeout
            )

    @pytest.mark.parametrize(
        "bad_path", ["", "/abs/path.md", "../secret", "a//b", "a/./b", "a/../b"]
    )
    def test_path_traversal_rejected(self, git_env: dict, bad_path: str) -> None:
        with pytest.raises(ValueError, match="path is invalid"):
            parse_git_glossary(url="https://example.com/repo.git", path=bad_path)

    def test_unallowlisted_host_rejected(self, git_env: dict) -> None:
        with pytest.raises(ValueError, match="not allowed"):
            parse_git_glossary(url="https://unallowlisted-domain.com/repo.git")


class TestGitFetch:
    def test_reads_arrow_pairs_from_raw_payload(self, git_env: dict) -> None:
        git_env["stdout"] = b"Hello -> Hola\nWorld -> Mundo\n"
        summary = parse_git_glossary(url="https://example.com/repo.git")
        pairs = {entry["source"]: entry["target"] for entry in summary.entries}
        assert pairs == {"Hello": "Hola", "World": "Mundo"}
        assert summary.format == "git_glossary"

    def test_reads_tar_archive_member(self, git_env: dict) -> None:
        git_env["stdout"] = _tar_with("GLOSSARY.md", b"Hello -> Hola\n")
        summary = parse_git_glossary(url="https://example.com/repo.git")
        assert [entry["source"] for entry in summary.entries] == ["Hello"]

    def test_parses_markdown_table_rows(self, git_env: dict) -> None:
        git_env["stdout"] = (
            b"| Source | Target |\n"
            b"|--------|--------|\n"
            b"| Hello | Hola |\n"
            b"| World | Mundo |\n"
        )
        summary = parse_git_glossary(url="https://example.com/repo.git")
        pairs = {entry["source"]: entry["target"] for entry in summary.entries}
        assert pairs == {"Hello": "Hola", "World": "Mundo"}

    def test_empty_archive_rejected(self, git_env: dict) -> None:
        git_env["stdout"] = b""
        with pytest.raises(ValueError, match="did not contain"):
            parse_git_glossary(url="https://example.com/repo.git")

    def test_no_valid_pairs_rejected(self, git_env: dict) -> None:
        git_env["stdout"] = b"# nothing but a comment\n"
        with pytest.raises(ValueError, match="no valid pairs"):
            parse_git_glossary(url="https://example.com/repo.git")

    def test_subprocess_errors_map_to_stable_messages(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(git_repo_mod, "_ssrf_blocked", lambda url: False)

        def missing_git(command, **kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr(git_repo_mod.subprocess, "run", missing_git)
        with pytest.raises(FormatNotAvailableError):
            parse_git_glossary(url="https://example.com/repo.git")

        def slow_git(command, **kwargs):
            raise subprocess.TimeoutExpired(command, 30)

        monkeypatch.setattr(git_repo_mod.subprocess, "run", slow_git)
        with pytest.raises(ValueError, match="timed out"):
            parse_git_glossary(url="https://example.com/repo.git")

        def failing_git(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)

        monkeypatch.setattr(git_repo_mod.subprocess, "run", failing_git)
        with pytest.raises(ValueError, match="could not be fetched"):
            parse_git_glossary(url="https://example.com/repo.git")


class TestGitCredentials:
    def test_credentials_land_in_remote_url_not_summary(self, git_env: dict) -> None:
        import base64

        git_env["stdout"] = b"Hello -> Hola\n"
        summary = parse_git_glossary(
            url="https://github.com/org/repo.git",
            credentials="user:supersecret",
        )
        command = " ".join(git_env["command"])
        assert "supersecret" not in command
        assert "--remote=https://github.com/org/repo.git" in command
        env = git_env.get("env") or {}
        assert env.get("GIT_CONFIG_COUNT") == "1"
        assert env.get("GIT_CONFIG_KEY_0") == "http.extraHeader"
        expected_auth = base64.b64encode(b"user:supersecret").decode("ascii")
        assert f"Authorization: Basic {expected_auth}" in env.get(
            "GIT_CONFIG_VALUE_0", ""
        )
        # The summary flows into API responses — it must stay redacted.
        assert "supersecret" not in (summary.source_uri or "")

    def test_credentials_rejected_for_non_http(self, git_env: dict) -> None:
        with pytest.raises(ValueError, match="only for HTTP"):
            parse_git_glossary(
                url="ssh://git@github.com/org/repo.git", credentials="user:secret"
            )

    @pytest.mark.parametrize("bad_creds", ["nocolon", "user@", ":secret", "user:"])
    def test_malformed_credentials_rejected(
        self, git_env: dict, bad_creds: str
    ) -> None:
        with pytest.raises(ValueError, match="username:secret"):
            parse_git_glossary(
                url="https://example.com/repo.git", credentials=bad_creds
            )


# ---------------------------------------------------------------------------
# Git ref flag-injection (merged from test_phase2_git_ref_injection.py,
# audit-secondary F26 / Phase 2)
# ---------------------------------------------------------------------------


def test_git_glossary_ref_flag_injection_rejected():
    """``parse_git_glossary`` validates the ``ref`` parameter against a
    safe-character set before passing it to ``git archive``, blocking
    flag injection (``--output=/tmp/pwn``) and shell metachar injection
    (``HEAD; rm -rf /``).
    """
    with pytest.raises(ValueError, match="Git ref is invalid or malformed"):
        parse_git_glossary(
            url="https://github.com/org/repo.git", ref="--output=/tmp/pwn"
        )

    with pytest.raises(ValueError, match="Git ref is invalid or malformed"):
        parse_git_glossary(url="https://github.com/org/repo.git", ref="-f")

    with pytest.raises(ValueError, match="Git ref is invalid or malformed"):
        parse_git_glossary(url="https://github.com/org/repo.git", ref="HEAD; rm -rf /")
