"""Pin test — callback URL SSRF / format validation via ``_is_url_safe``.

Verifies:
- Private RFC-1918 IPs blocked
- Loopback (127.x) blocked
- Link-local (169.254.x) blocked
- Blocked ports (Redis 6379, PG 5432, etc.) rejected
- Non-http/https schemes rejected
- Missing hostname rejected
- Public HTTPS URL accepted (happy path)

These tests stub ``asyncio.get_running_loop().getaddrinfo`` so no real DNS
lookup is needed. The test is synchronous-friendly via a minimal event loop.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragbot.shared.callback_validator import _is_url_safe


def _addr_info(ip: str):
    """Build the minimal addrinfo tuple shape returned by ``getaddrinfo``."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 80))]


def _run(coro):
    return asyncio.run(coro)


def test_public_https_url_is_safe():
    """A public HTTPS URL resolving to a routable IP is accepted."""
    with patch("ragbot.shared.callback_validator.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=_addr_info("93.184.216.34"))  # example.com
        ok, reason = _run(_is_url_safe("https://example.com/webhook"))
    assert ok is True


def test_private_ip_10_x_blocked():
    """URLs resolving to 10.x.x.x are SSRF-blocked."""
    with patch("ragbot.shared.callback_validator.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=_addr_info("10.0.1.200"))
        ok, reason = _run(_is_url_safe("https://internal.corp/api"))
    assert ok is False
    assert "blocked" in reason.lower() or "10.0.1.200" in reason


def test_private_ip_192_168_blocked():
    """URLs resolving to 192.168.x.x are SSRF-blocked."""
    with patch("ragbot.shared.callback_validator.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=_addr_info("192.168.0.1"))
        ok, reason = _run(_is_url_safe("https://router.local/"))
    assert ok is False


def test_loopback_127_blocked():
    """Loopback 127.x.x.x is SSRF-blocked."""
    with patch("ragbot.shared.callback_validator.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=_addr_info("127.0.0.1"))
        ok, reason = _run(_is_url_safe("http://localhost/callback"))
    assert ok is False


def test_link_local_169_254_blocked():
    """Link-local 169.254.x.x (cloud metadata / CGNAT overlap) is blocked."""
    with patch("ragbot.shared.callback_validator.asyncio.get_running_loop") as mock_loop:
        mock_loop.return_value.getaddrinfo = AsyncMock(return_value=_addr_info("169.254.169.254"))
        ok, reason = _run(_is_url_safe("http://169.254.169.254/latest/meta-data/"))
    assert ok is False


def test_blocked_port_redis_6379():
    """Port 6379 (Redis) is rejected without DNS resolution."""
    ok, reason = _run(_is_url_safe("https://public.example.com:6379/cmd"))
    assert ok is False
    assert "6379" in reason


def test_blocked_port_postgres_5432():
    """Port 5432 (PostgreSQL) is rejected without DNS resolution."""
    ok, reason = _run(_is_url_safe("http://db.internal.example.com:5432/query"))
    assert ok is False
    assert "5432" in reason


def test_invalid_scheme_ftp_rejected():
    """Non-http/https scheme (ftp://) is rejected immediately."""
    ok, reason = _run(_is_url_safe("ftp://example.com/file"))
    assert ok is False
    assert "scheme" in reason.lower() or "ftp" in reason


def test_missing_hostname_rejected():
    """URL without a hostname is rejected immediately."""
    ok, reason = _run(_is_url_safe("https:///no-host"))
    assert ok is False
