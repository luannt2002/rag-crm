"""Tests for SSRF protection in callback validation."""
import asyncio
import socket

import pytest
from unittest.mock import AsyncMock, patch
from ragbot.shared.callback_validator import _is_url_safe, _check_ip_blocked

import ipaddress


class TestCheckIpBlocked:
    def test_blocks_private_10(self):
        assert _check_ip_blocked(ipaddress.ip_address("10.0.0.1"))

    def test_blocks_private_172(self):
        assert _check_ip_blocked(ipaddress.ip_address("172.16.0.1"))

    def test_blocks_private_192(self):
        assert _check_ip_blocked(ipaddress.ip_address("192.168.1.1"))

    def test_blocks_loopback(self):
        assert _check_ip_blocked(ipaddress.ip_address("127.0.0.1"))

    def test_blocks_metadata(self):
        assert _check_ip_blocked(ipaddress.ip_address("169.254.169.254"))

    def test_blocks_cgnat(self):
        assert _check_ip_blocked(ipaddress.ip_address("100.64.0.1"))

    def test_allows_public(self):
        assert not _check_ip_blocked(ipaddress.ip_address("8.8.8.8"))

    def test_blocks_ipv4_mapped_ipv6(self):
        """::ffff:10.0.0.1 should be blocked (IPv4-mapped IPv6)."""
        assert _check_ip_blocked(ipaddress.ip_address("::ffff:10.0.0.1"))

    def test_blocks_ipv6_loopback(self):
        assert _check_ip_blocked(ipaddress.ip_address("::1"))


class TestIsUrlSafe:
    @pytest.mark.asyncio
    async def test_blocks_private_10_range(self):
        async def mock_getaddrinfo(*a, **kw):
            return [(2, 1, 6, '', ('10.0.0.1', 0))]
        with patch.object(asyncio.get_event_loop(), "getaddrinfo", side_effect=mock_getaddrinfo):
            safe, reason = await _is_url_safe("http://internal.corp/hook")
            assert not safe
            assert "blocked" in reason.lower()

    @pytest.mark.asyncio
    async def test_allows_public_ip(self):
        async def mock_getaddrinfo(*a, **kw):
            return [(2, 1, 6, '', ('8.8.8.8', 0))]
        with patch.object(asyncio.get_event_loop(), "getaddrinfo", side_effect=mock_getaddrinfo):
            safe, reason = await _is_url_safe("https://api.example.com/webhook")
            assert safe

    @pytest.mark.asyncio
    async def test_rejects_invalid_scheme(self):
        safe, reason = await _is_url_safe("ftp://files.example.com/data")
        assert not safe
        assert "scheme" in reason.lower()

    @pytest.mark.asyncio
    async def test_rejects_no_hostname(self):
        safe, reason = await _is_url_safe("http:///path")
        assert not safe

    @pytest.mark.asyncio
    async def test_dns_resolution_failure(self):
        async def mock_getaddrinfo(*a, **kw):
            raise socket.gaierror("DNS failed")
        with patch.object(asyncio.get_event_loop(), "getaddrinfo", side_effect=mock_getaddrinfo):
            safe, reason = await _is_url_safe("http://nonexistent.invalid/hook")
            assert not safe
