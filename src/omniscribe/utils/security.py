"""URL/host security primitives: SSRF guard with DNS-pinning support.

Public API
----------
- :class:`SSRFCheckResult` — structured outcome of an SSRF check, including
  the IP that was validated so callers can pin the TCP connection to it
  (TOCTOU defense).
- :func:`is_ssrf_target` — async guard that validates a URL and returns
  the result.

The contract is "fail closed": malformed URLs, unsupported schemes, and
DNS resolution failures return ``SSRFCheckResult(allowed=False, ...)`` so
caller-supplied endpoints cannot slip through. The :attr:`resolved_ip`
slot is populated whenever ``allowed`` is True so the caller can hand
the same IP to the HTTP transport and bypass DNS re-resolution.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

from omniscribe.config import load_settings


def _local_ssrf_allowed() -> bool:
    # Resolved via the validated RuntimeSettings (audit L-1) so the SSRF
    # guard honors the same env-var contract as the rest of the config
    # surface (``ALLOW_SSRF_LOCAL``) without re-parsing here.
    return load_settings().allow_ssrf_local


_UNCONDITIONAL_BLOCKED_NETWORKS: Final[
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
] = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::/64"),
)


def _is_unconditionally_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    normalized: ipaddress.IPv4Address | ipaddress.IPv6Address = (
        ip.ipv4_mapped
        if (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
        else ip
    )
    if str(normalized).startswith("169.254.") or str(normalized) == "0.0.0.0":
        return True
    for net in _UNCONDITIONAL_BLOCKED_NETWORKS:
        if normalized.version == net.version and normalized in net:
            return True
    return False


def _is_cloud_metadata(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    normalized: ipaddress.IPv4Address | ipaddress.IPv6Address = (
        ip.ipv4_mapped
        if (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
        else ip
    )
    return normalized.is_link_local or str(normalized).startswith("169.254.")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    normalized: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        normalized = ip.ipv4_mapped
    if str(normalized) == "0.0.0.0":
        return True
    # Deliberate security stance (audit 1.19): normalized.is_reserved includes
    # 240.0.0.0/4 and CGNAT (100.64.0.0/10), which are deliberately blocked as
    # non-public IP targets unless ALLOW_SSRF_LOCAL is enabled for local ranges.
    return bool(
        normalized.is_unspecified
        or normalized.is_reserved
        or normalized.is_private
        or normalized.is_loopback
        or normalized.is_link_local
        or normalized.is_multicast
    )


@dataclass(frozen=True)
class SSRFCheckResult:
    """Structured outcome of :func:`is_ssrf_target`.

    ``allowed`` is True only when the URL is safe to fetch AND a concrete
    ``resolved_ip`` is available for the caller to pin the TCP connection
    to. For URL-as-literal-IP inputs, ``resolved_ip`` is the literal IP.
    For DNS-resolved hosts it is one of the validated addresses, chosen by
    :func:`_pick_pinned_address` (a transport that pins this IP neutralises
    the DNS-rebinding TOCTOU window that exists when the HTTP client
    re-resolves the hostname on connect).

    ``reason`` is a short, stable tag describing the failure mode when
    ``allowed`` is False (used for logging / metrics). It is ``None`` for
    successful checks.
    """

    allowed: bool
    resolved_ip: str | None
    reason: str | None = None


async def _resolve_host(host: str) -> list[tuple[str, int]]:
    """Resolve ``host`` off the event loop to avoid blocking it.

    Returns a list of resolved ``(address, port)`` pairs (port unused), or
    an empty list on failure.
    """
    try:
        addr_info = await asyncio.to_thread(socket.getaddrinfo, host, None)
    except (socket.gaierror, Exception):
        return []
    resolved: list[tuple[str, int]] = []
    for _family, _st, _p, _cn, sockaddr in addr_info:
        # IPv4 sockaddr is 2-tuple (str, int); IPv6 is 4-tuple (str, int, int, int)
        # (or the legacy 4-tuple (bytes, int, int, int) on some platforms).
        # We only use the address slot, so normalise both shapes to (str, int).
        raw_address = sockaddr[0]
        port = int(sockaddr[1])
        address: str
        if isinstance(raw_address, bytes):
            address = raw_address.decode("utf-8", errors="replace")
        else:
            # mypy's getaddrinfo stub types sockaddr[0] as `str | int`; in
            # practice it's always str at this point, so cast to lock that in.
            address = str(raw_address)
        resolved.append((address, port))
    return resolved


def _pick_pinned_address(
    candidates: list[tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, str | None]],
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, str | None]:
    """Choose which validated address the transport should pin to.

    Local model servers (LM Studio, Ollama) bind IPv4-only while Windows
    ``getaddrinfo("localhost")`` lists ``::1`` first, so pinning the head of
    the answer made every loopback discovery call fail with a connection
    refusal. When the whole candidate set is non-public we therefore prefer
    the first IPv4; a set containing any public address keeps resolution
    order so public traffic is never re-pointed at a different host.
    """
    if all(_is_blocked_ip(ip) for ip, _reason in candidates):
        for candidate in candidates:
            if candidate[0].version == 4:
                return candidate
    return candidates[0]


async def is_ssrf_target(url: str | None) -> SSRFCheckResult:
    """Validate a URL against the SSRF blocklist and return the validated IP.

    Returns an :class:`SSRFCheckResult` whose :attr:`allowed` is True only
    when the URL passes every check AND a concrete IP is available. The
    :attr:`resolved_ip` slot is the IP the caller should pin the TCP
    connection to (TOCTOU defense). For invalid / blocked / unresolved
    inputs, ``allowed`` is False and ``resolved_ip`` is None.
    """
    if not url:
        return SSRFCheckResult(False, None, "empty-url")

    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return SSRFCheckResult(False, None, "unsupported-scheme")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return SSRFCheckResult(False, None, "empty-host")

        allow_local = _local_ssrf_allowed()

        # Block cloud metadata endpoints regardless of local-development mode.
        if host == "metadata.google.internal":
            return SSRFCheckResult(False, None, "metadata-endpoint")

        # Literal IP in the URL — no DNS involved, so TOCTOU is moot.
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None

        if ip is not None:
            if _is_cloud_metadata(ip):
                return SSRFCheckResult(False, None, "metadata-endpoint")
            if _is_unconditionally_blocked(ip):
                return SSRFCheckResult(False, None, "literal-blocked-ip")
            if _is_blocked_ip(ip):
                if not allow_local:
                    return SSRFCheckResult(False, None, "literal-blocked-ip")
                return SSRFCheckResult(True, str(ip), "literal-blocked-but-allowed")
            return SSRFCheckResult(True, str(ip))

        if (host == "localhost" or host.endswith(".local")) and not allow_local:
            return SSRFCheckResult(False, None, "localhost-blocked")
        # Fall through to DNS resolution when allow_local is True —
        # the transport still needs an IP to pin, and 127.0.0.1 / ::1
        # is what getaddrinfo returns.

        resolved = await _resolve_host(host)
        if not resolved:
            return SSRFCheckResult(False, None, "dns-resolution-failed")

        # Validate EVERY resolved address before picking one to pin. The
        # pinned IP is the only host the transport will contact, so
        # skipping validation of the tail of a DNS answer would leave a
        # DNS-rebinding hole. If ANY resolved IP is blocked the URL is
        # blocked (unless the caller opted in to local addresses).
        candidates: list[
            tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, str | None]
        ] = []
        for address, _port in resolved:
            try:
                resolved_ip = ipaddress.ip_address(address)
            except ValueError:
                return SSRFCheckResult(False, None, "invalid-resolved-ip")
            if _is_cloud_metadata(resolved_ip):
                return SSRFCheckResult(False, None, "metadata-endpoint")
            if _is_unconditionally_blocked(resolved_ip):
                return SSRFCheckResult(False, None, "resolved-blocked-ip")
            if _is_blocked_ip(resolved_ip):
                if not allow_local:
                    return SSRFCheckResult(False, None, "resolved-blocked-ip")
                candidates.append((resolved_ip, "resolved-blocked-but-allowed"))
            else:
                candidates.append((resolved_ip, None))

        if not candidates:
            return SSRFCheckResult(False, None, "dns-resolution-failed")

        chosen_ip, reason = _pick_pinned_address(candidates)
        return SSRFCheckResult(True, str(chosen_ip), reason)
    except Exception as exc:
        return SSRFCheckResult(False, None, f"unexpected-error: {exc}")


def is_blocked_host(host: str | None) -> bool:
    """Synchronously check if a hostname/IP is a blocked private or metadata target.

    Warning: This function performs synchronous DNS resolution (`socket.getaddrinfo`)
    which can block the event loop for seconds. Do NOT call this directly from an
    async event loop thread; use `check_ssrf_target` or `asyncio.to_thread` instead.
    """
    if not host:
        return False
    h = host.strip().lower()
    if h == "metadata.google.internal":
        return True
    allow_local = _local_ssrf_allowed()
    try:
        ip = ipaddress.ip_address(h)
        if _is_cloud_metadata(ip) or _is_unconditionally_blocked(ip):
            return True
        if _is_blocked_ip(ip):
            return not allow_local
        return False
    except ValueError:
        pass
    if (h == "localhost" or h.endswith(".local")) and not allow_local:
        return True
    try:
        addr_info = socket.getaddrinfo(h, None)
        if not addr_info:
            return True
        for _fam, _st, _p, _cn, sockaddr in addr_info:
            raw_addr = sockaddr[0]
            if isinstance(raw_addr, bytes):
                raw_addr = raw_addr.decode("utf-8", errors="replace")
            resolved_ip = ipaddress.ip_address(raw_addr)
            if _is_cloud_metadata(resolved_ip) or _is_unconditionally_blocked(
                resolved_ip
            ):
                return True
            if _is_blocked_ip(resolved_ip) and not allow_local:
                return True
    except Exception:
        return True
    return False


def check_ssrf_target_sync(url: str | None) -> SSRFCheckResult:
    """Synchronously validate a URL against the SSRF guard.

    Safe to call from both synchronous code without an event loop and
    synchronous callbacks running inside an active event loop (uses a worker
    thread in the latter case to prevent event loop blocking/nesting errors).
    """
    if not url:
        return SSRFCheckResult(False, None, "empty-url")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(is_ssrf_target(url))
    future = _SSRF_EXECUTOR.submit(asyncio.run, is_ssrf_target(url))
    return future.result()


# Module-level executor (pedantic 1.18): the previous ``with
# ThreadPoolExecutor(max_workers=1) as executor:`` block allocated and
# tore down a thread pool on every call. SSRF checks live on the OCR
# request hot path (``plugins/ocr/pipeline_bridge.py`` and
# ``plugins/ocr/service.py``), so each request paid for a fresh
# pool + worker + future. The executor is sized at 2 because the work
# is dominated by ``socket.getaddrinfo`` (blocking DNS); two workers
# overlap the next call's DNS lookup with the previous call's return
# without unbounded growth. Lifetime is the process; if process-shutdown
# ordering ever needs to be precise, register an ``atexit`` cleanup.
_SSRF_EXECUTOR: Final[ThreadPoolExecutor] = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="omniscribe-ssrf-check"
)
