"""SSRF fail-closed guard safety tests.

Split out of the former monolithic ``tests/test_api_safety.py``.
"""

from __future__ import annotations

import os
import socket
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")

from omniscribe.utils.security import is_blocked_host, is_ssrf_target


def _public_dns(host: str, port, *args, **kwargs):
    """Stub ``socket.getaddrinfo``: only ``api.openai.com`` resolves."""
    if host == "api.openai.com":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.18.3.161", 443))]
    raise socket.gaierror(-2, "Name or service not known")


async def test_ssrf_fails_closed_and_requires_explicit_local_allowance():
    # Sprint 4 / M-6 audit fix: convert ``asyncio.run`` calls inside the
    # SSRF safety test to ``await`` so the test runs on the same loop
    # as the rest of the suite. Auto mode (set in pyproject.toml) drives
    # the coroutine without an explicit decorator.
    with patch.dict(os.environ, {}, clear=True):
        with patch("omniscribe.utils.security.socket.getaddrinfo") as getaddrinfo:
            getaddrinfo.side_effect = _public_dns
            public = await is_ssrf_target("http://api.openai.com/v1")
            assert public.allowed is True
            assert public.resolved_ip == "104.18.3.161"
            assert (await is_ssrf_target("localhost:1234/v1")).allowed is False
            assert (await is_ssrf_target("ftp://api.openai.com/v1")).allowed is False
            assert (await is_ssrf_target(None)).allowed is False

    with patch.dict(os.environ, {}, clear=True):
        with patch("omniscribe.utils.security.socket.getaddrinfo") as getaddrinfo:
            getaddrinfo.side_effect = socket.gaierror(-2, "Name or service not known")
            assert (
                await is_ssrf_target("http://does-not-resolve.example/v1")
            ).allowed is False

    with patch.dict(os.environ, {"ALLOW_SSRF_LOCAL": "true"}, clear=True):
        loopback = await is_ssrf_target("http://127.0.0.1:1234/v1")
        assert loopback.allowed is True
        assert loopback.resolved_ip == "127.0.0.1"
        assert (
            await is_ssrf_target("http://metadata.google.internal/v1")
        ).allowed is False


# ---------------------------------------------------------------------------
# Merged from test_phase2_cloud_metadata_blocked.py (audit-secondary F26)
# ---------------------------------------------------------------------------


async def test_cloud_metadata_unconditionally_blocked(monkeypatch):
    """Verify 169.254.169.254 is rejected even if ALLOW_SSRF_LOCAL is true.

    The original fix: the SSRF guard used to allow the cloud metadata
    endpoint (``169.254.169.254``) when ``ALLOW_SSRF_LOCAL=true``. The
    fix makes the cloud-metadata block unconditional — it is a
    credential-leak vector on every major cloud, and the local-dev
    default should not relax it.
    """
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "true")

    res = await is_ssrf_target("http://169.254.169.254/latest/meta-data/")
    assert not res.allowed
    assert res.reason == "metadata-endpoint"

    assert is_blocked_host("169.254.169.254")
    assert is_blocked_host("metadata.google.internal")


# ---------------------------------------------------------------------------
# Pedantic 1.18: module-level SSRF executor singleton
# ---------------------------------------------------------------------------


def test_check_ssrf_target_sync_uses_module_level_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``check_ssrf_target_sync`` must reuse a module-level executor
    instead of allocating a fresh ``ThreadPoolExecutor`` per call.

    The previous code's ``with ThreadPoolExecutor(max_workers=1)``
    block paid a thread-pool + worker + future setup/teardown on every
    SSRF check; on the OCR request hot path this was both wasteful and
    noisy in thread-dump output. The fix hoists a single executor to
    module scope.

    The check: count ``ThreadPoolExecutor.__init__`` invocations while
    issuing several ``check_ssrf_target_sync`` calls; the count must
    be zero (the executor was created at module import, not on demand).
    """
    from concurrent.futures import ThreadPoolExecutor

    import omniscribe.utils.security as security

    init_calls: list[tuple] = []

    real_init = ThreadPoolExecutor.__init__

    def counting_init(self, *args, **kwargs):
        init_calls.append((args, kwargs))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(ThreadPoolExecutor, "__init__", counting_init)

    # Stub ``is_ssrf_target`` so we don't need a real DNS lookup. The
    # sync wrapper still routes through the executor.
    async def _fake_ssrf(_url):
        from omniscribe.utils.security import SSRFCheckResult

        return SSRFCheckResult(True, "93.184.216.34")

    monkeypatch.setattr(security, "is_ssrf_target", _fake_ssrf)

    # The executor is reused across calls: zero new ThreadPoolExecutor
    # instantiations per call, regardless of the loop state.
    for _ in range(5):
        result = security.check_ssrf_target_sync("http://example.com")
        assert result.allowed is True
        assert result.resolved_ip == "93.184.216.34"

    assert init_calls == [], (
        f"check_ssrf_target_sync allocated {len(init_calls)} new "
        f"ThreadPoolExecutor(s); expected to reuse the module-level one"
    )

    # The module-level executor is still the one in use, and it is the
    # same object a fresh import would expose.
    assert isinstance(security._SSRF_EXECUTOR, ThreadPoolExecutor)
    assert security._SSRF_EXECUTOR._max_workers >= 1


def test_is_blocked_host_docstring_warning() -> None:
    doc = is_blocked_host.__doc__ or ""
    normalized_doc = " ".join(doc.split())
    assert (
        "Warning: This function performs synchronous DNS resolution (`socket.getaddrinfo`) "
        "which can block the event loop for seconds. Do NOT call this directly from an "
        "async event loop thread; use `check_ssrf_target` or `asyncio.to_thread` instead."
    ) in normalized_doc


def test_is_blocked_ip_reserved_and_cgnat(monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress

    from omniscribe.utils.security import _is_blocked_ip, is_blocked_host

    # Deliberate security stance (audit 1.19):
    # normalized.is_reserved includes 240.0.0.0/4, which is deliberately blocked
    # as a non-public IP target unless ALLOW_SSRF_LOCAL is enabled.
    assert _is_blocked_ip(ipaddress.ip_address("240.0.0.1")) is True

    # CGNAT (100.64.0.0/10) is unconditionally blocked regardless of ALLOW_SSRF_LOCAL
    assert is_blocked_host("100.64.0.1") is True

    # Reserved IP (240.0.0.1) is blocked when ALLOW_SSRF_LOCAL is false
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")
    assert is_blocked_host("240.0.0.1") is True

    # When ALLOW_SSRF_LOCAL is true, local ranges are allowed for development
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "true")
    assert is_blocked_host("240.0.0.1") is False
    # CGNAT stays blocked even when ALLOW_SSRF_LOCAL is true
    assert is_blocked_host("100.64.0.1") is True


# ---------------------------------------------------------------------------
# Pin-target selection for multi-address DNS results
# ---------------------------------------------------------------------------


def _dns_for(*addresses: str):
    """Build a ``socket.getaddrinfo`` stub returning ``addresses`` in order."""
    import ipaddress as _ip

    def _resolve(host, port, *args, **kwargs):
        return [
            (
                socket.AF_INET6 if _ip.ip_address(a).version == 6 else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (a, 0, 0, 0) if _ip.ip_address(a).version == 6 else (a, 0),
            )
            for a in addresses
        ]

    return _resolve


async def test_dual_stack_localhost_pins_ipv4(monkeypatch: pytest.MonkeyPatch) -> None:
    """``localhost`` must pin to 127.0.0.1, not the ::1 that Windows lists first.

    Local model servers (LM Studio, Ollama) bind IPv4-only, so pinning the
    TCP connection to the first address ``getaddrinfo`` returns made every
    ``/api/providers/{id}/models`` discovery call fail with a connection
    refusal on Windows.
    """
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "true")

    with patch(
        "omniscribe.utils.security.socket.getaddrinfo",
        side_effect=_dns_for("::1", "127.0.0.1"),
    ):
        res = await is_ssrf_target("http://localhost:1234/v1/models")

    assert res.allowed is True
    assert res.resolved_ip == "127.0.0.1"


async def test_ipv6_only_host_still_pins_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no IPv4 candidate the guard must still return the IPv6 loopback."""
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "true")

    with patch(
        "omniscribe.utils.security.socket.getaddrinfo",
        side_effect=_dns_for("::1"),
    ):
        res = await is_ssrf_target("http://localhost:1234/v1/models")

    assert res.allowed is True
    assert res.resolved_ip == "::1"


async def test_public_host_keeps_resolution_order() -> None:
    """IPv4 preference must not reorder candidates for non-local hosts."""
    with patch(
        "omniscribe.utils.security.socket.getaddrinfo",
        side_effect=_dns_for("104.18.3.161", "2606:2800:220:1:26:2ff:fe72:c9c0"),
    ):
        res = await is_ssrf_target("http://api.openai.com/v1")

    assert res.allowed is True
    assert res.resolved_ip == "104.18.3.161"


async def test_metadata_in_multi_address_result_still_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every resolved address is validated, not just the one that gets pinned."""
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "true")

    with patch(
        "omniscribe.utils.security.socket.getaddrinfo",
        side_effect=_dns_for("127.0.0.1", "169.254.169.254"),
    ):
        res = await is_ssrf_target("http://localhost:1234/v1/models")

    assert res.allowed is False
    assert res.resolved_ip is None


async def test_dual_stack_loopback_names_blocked_without_local_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname resolving only to loopback stays rejected without opt-in."""
    monkeypatch.setenv("ALLOW_SSRF_LOCAL", "false")

    with patch(
        "omniscribe.utils.security.socket.getaddrinfo",
        side_effect=_dns_for("::1", "127.0.0.1"),
    ):
        res = await is_ssrf_target("http://my.local.dev:1234/v1/models")

    assert res.allowed is False
    assert res.reason == "resolved-blocked-ip"
