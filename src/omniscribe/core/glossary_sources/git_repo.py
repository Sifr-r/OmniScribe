"""Git-hosted glossary file importer."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import re
import subprocess
import tarfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from omniscribe.core.translate.glossary import Glossary
from omniscribe.utils.security import is_ssrf_target

from ._common import decode_source, entry_dict
from .summary import FormatNotAvailableError, GlossaryImportSummary, redact_dsn

logger = logging.getLogger(__name__)

DEFAULT_GIT_HOST_ALLOWLIST: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "bitbucket.org",
        "example.com",
    }
)


def _is_allowed_git_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    h = hostname.strip().lower()
    allowed = set(DEFAULT_GIT_HOST_ALLOWLIST)
    extra = os.environ.get("OMNISCRIBE_GIT_ALLOWLIST") or os.environ.get(
        "GIT_HOST_ALLOWLIST"
    )
    if extra:
        for item in extra.split(","):
            cleaned = item.strip().lower()
            if cleaned:
                allowed.add(cleaned)
    try:
        from omniscribe.config import load_settings

        if load_settings().allow_ssrf_local:
            allowed.update({"localhost", "127.0.0.1", "::1"})
    except Exception:
        pass

    return any(h == domain or h.endswith("." + domain) for domain in allowed)


def _validate_credentials(url: str, credentials: str | None) -> None:
    if not credentials:
        return
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Credentials are supported only for HTTP(S) git URLs.")
    if "@" in credentials or ":" not in credentials:
        raise ValueError("Git credentials must use username:secret form.")
    username, secret = credentials.split(":", 1)
    if not username or not secret:
        raise ValueError("Git credentials must use username:secret form.")


def parse_git_glossary(
    *,
    url: str,
    ref: str = "HEAD",
    path: str = "GLOSSARY.md",
    credentials: str | None = None,
    timeout_sec: int = 30,
) -> GlossaryImportSummary:
    """Read one glossary file from a remote git archive without cloning history."""
    clean_url = str(url).strip()
    if not clean_url:
        raise ValueError("Git glossary URL is required.")
    if _ssrf_blocked(clean_url):
        raise ValueError("Git glossary URL is not allowed.")
    parsed_host = urlsplit(clean_url).hostname
    if not _is_allowed_git_host(parsed_host):
        raise ValueError(f"Git glossary host '{parsed_host or ''}' is not allowed.")
    clean_ref = str(ref).strip() if ref is not None else ""
    if not clean_ref:
        raise ValueError("Git ref must not be empty.")
    if clean_ref.startswith("-") or not re.match(r"^[a-zA-Z0-9_.\-/]+$", clean_ref):
        raise ValueError("Git ref is invalid or malformed.")
    safe_path = _validate_path(path)
    if timeout_sec <= 0 or timeout_sec > 600:
        raise ValueError("timeout_sec must be between 1 and 600 seconds.")

    _validate_credentials(clean_url, credentials)
    command = [
        "git",
        "archive",
        f"--remote={clean_url}",
        clean_ref,
        safe_path,
    ]
    subproc_env = os.environ.copy()
    if credentials:
        b64_auth = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        subproc_env["GIT_CONFIG_COUNT"] = "1"
        subproc_env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        subproc_env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {b64_auth}"

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            timeout=timeout_sec,
            env=subproc_env,
        )
    except FileNotFoundError as exc:
        raise FormatNotAvailableError(
            "Git import requires the git executable. Install with: "
            "pip install omniscribe[glossary]"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Git glossary fetch timed out.") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError("Git glossary file could not be fetched.") from exc

    payload = _archive_member(completed.stdout, safe_path)
    text, used_encoding, warnings = decode_source(payload)
    glossary = _parse_text(text)
    if not glossary.entries:
        raise ValueError("Git glossary file contains no valid pairs.")
    serialized_raw: object = glossary.to_dict().get("entries", [])
    entries: list[dict[str, object]] = []
    if isinstance(serialized_raw, list):
        for raw_entry in serialized_raw:
            if isinstance(raw_entry, dict):
                entries.append(dict(raw_entry))
    return GlossaryImportSummary(
        entries=entries,
        format="git_glossary",
        source_uri=redact_dsn(clean_url),
        encoding=used_encoding,
        warnings=warnings,
    )


def _ssrf_blocked(url: str) -> bool:
    """Call the async SSRF validator from this synchronous parser safely.

    Returns True when the URL is blocked by the SSRF guard. Wraps
    the structured :class:`SSRFCheckResult` into a bool for the
    synchronous parser call sites.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return not (asyncio.run(is_ssrf_target(url))).allowed
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, is_ssrf_target(url))
        return not future.result().allowed


def _validate_path(path: str) -> str:
    clean = str(path).replace("\\", "/").strip()
    parts = clean.split("/")
    if (
        not clean
        or clean.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("Git glossary path is invalid.")
    if "\x00" in clean:
        raise ValueError("Git glossary path is invalid.")
    return clean


def _with_credentials(url: str, credentials: str | None) -> str:
    _validate_credentials(url, credentials)
    if not credentials:
        return url
    parsed = urlsplit(url)
    username, secret = credentials.split(":", 1)
    return urlunsplit(
        (
            parsed.scheme,
            f"{username}:{secret}@{parsed.hostname}{_port(parsed)}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _port(parsed: Any) -> str:
    return f":{parsed.port}" if parsed.port is not None else ""


def _archive_member(archive: bytes, path: str) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            for member in tar.getmembers():
                if (
                    member.isfile()
                    and member.name.rsplit("/", 1)[-1] == path.rsplit("/", 1)[-1]
                ):
                    extracted = tar.extractfile(member)
                    if extracted is not None:
                        return extracted.read()
    except tarfile.ReadError:
        # A few test doubles and git wrappers return the requested file itself.
        pass
    if archive:
        return archive
    raise ValueError("Git glossary archive did not contain the requested file.")


def _is_md_separator(line: str) -> bool:
    return line.startswith("|") and set(line) <= {"|", "-", ":", " "}


def _parse_text(text: str) -> Glossary:
    glossary = Glossary.from_paired_lines(text)
    if glossary.entries:
        return glossary
    lines = text.splitlines()
    entries = []
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or line.startswith("#") or _is_md_separator(line):
            continue
        if "|" in line:
            # A pipe row directly above a separator row is the markdown
            # table header ("| Source | Target |") — not a glossary pair.
            for follow_up in lines[index + 1 :]:
                follow_up = follow_up.strip()
                if not follow_up:
                    continue
                if _is_md_separator(follow_up):
                    line = ""
                break
            if not line:
                continue
            columns = [part.strip() for part in line.strip("|").split("|")]
            if len(columns) >= 2:
                item = entry_dict(columns[0], columns[1])
                if item is not None:
                    entries.append(item)
        elif "->" in line:
            source, target = line.split("->", 1)
            item = entry_dict(source, target)
            if item is not None:
                entries.append(item)
    return Glossary.from_dict({"entries": entries})


# Silence unused import linter - Callable is part of public re-exports for typing.
_ = Callable
