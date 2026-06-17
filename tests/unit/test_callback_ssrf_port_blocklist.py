"""Tests for SSRF port blocklist in callback_validator."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_blocked_port_redis():
    """Callback URL targeting Redis port 6379 should be rejected."""
    from ragbot.shared.callback_validator import _is_url_safe

    ok, reason = await _is_url_safe("https://example.com:6379/webhook")
    assert ok is False
    assert "blocked port" in reason


@pytest.mark.asyncio
async def test_blocked_port_postgres():
    """Callback URL targeting PostgreSQL port 5432 should be rejected."""
    from ragbot.shared.callback_validator import _is_url_safe

    ok, reason = await _is_url_safe("https://example.com:5432/webhook")
    assert ok is False
    assert "blocked port" in reason


@pytest.mark.asyncio
async def test_blocked_port_mysql():
    """Callback URL targeting MySQL port 3306 should be rejected."""
    from ragbot.shared.callback_validator import _is_url_safe

    ok, reason = await _is_url_safe("https://example.com:3306/hook")
    assert ok is False
    assert "blocked port" in reason


@pytest.mark.asyncio
async def test_blocked_port_mongo():
    """Callback URL targeting MongoDB port 27017 should be rejected."""
    from ragbot.shared.callback_validator import _is_url_safe

    ok, reason = await _is_url_safe("https://example.com:27017/hook")
    assert ok is False
    assert "blocked port" in reason


@pytest.mark.asyncio
async def test_blocked_port_elasticsearch():
    """Callback URL targeting Elasticsearch port 9200 should be rejected."""
    from ragbot.shared.callback_validator import _is_url_safe

    ok, reason = await _is_url_safe("https://example.com:9200/hook")
    assert ok is False
    assert "blocked port" in reason


@pytest.mark.asyncio
async def test_blocked_port_memcached():
    """Callback URL targeting Memcached port 11211 should be rejected."""
    from ragbot.shared.callback_validator import _is_url_safe

    ok, reason = await _is_url_safe("https://example.com:11211/hook")
    assert ok is False
    assert "blocked port" in reason


@pytest.mark.asyncio
async def test_allowed_port_443():
    """Standard HTTPS port 443 should not be blocked by port check."""
    from ragbot.shared.callback_validator import _is_url_safe

    # Port 443 is allowed (may still fail DNS, but not port-blocked)
    ok, reason = await _is_url_safe("https://example.com:443/webhook")
    # Should not fail due to port blocklist
    assert "blocked port" not in reason


@pytest.mark.asyncio
async def test_no_port_specified():
    """URL without explicit port should not trigger port blocklist."""
    from ragbot.shared.callback_validator import _is_url_safe

    # No port in URL — should not hit port check (may fail on DNS/IP though)
    ok, reason = await _is_url_safe("https://example.com/webhook")
    assert "blocked port" not in reason


def test_blocked_ports_constant():
    """Verify the _BLOCKED_PORTS frozenset contains expected ports."""
    from ragbot.shared.callback_validator import _BLOCKED_PORTS

    expected = {6379, 5432, 3306, 27017, 9200, 11211}
    assert _BLOCKED_PORTS == expected
