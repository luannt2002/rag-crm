"""P25-L5 — Redis infra hardening regression tests.

Verifies that `create_redis_client` wires socket timeouts, health-check
interval, retry-on-timeout, and keepalive into the underlying connection
pool so a slow / restarted Redis never hangs application coroutines.

The optional integration test exercises a real local Redis (if reachable)
to confirm the configured client can still ping round-trip.
"""

from __future__ import annotations

import socket

import pytest

from ragbot.infrastructure.cache.redis_cache import create_redis_client


def _is_local_redis_up(host: str = "127.0.0.1", port: int = 6379) -> bool:
    """Best-effort probe to decide whether to run the integration test."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def test_redis_client_has_socket_timeout_configured() -> None:
    """P25-L5 guardrail — resilience kwargs must reach the pool.

    Regression for the pre-P25 default (`from_url(url, max_connections=...)`)
    which left Redis ops uncancellable. Any future refactor that drops these
    kwargs will be caught here.
    """
    client = create_redis_client("redis://localhost:6379/0", max_connections=25)
    try:
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs.get("socket_timeout") == 2.0, (
            f"socket_timeout must be 2.0s, got {kwargs.get('socket_timeout')!r}"
        )
        assert kwargs.get("socket_connect_timeout") == 1.0, (
            f"socket_connect_timeout must be 1.0s, "
            f"got {kwargs.get('socket_connect_timeout')!r}"
        )
        assert kwargs.get("health_check_interval") == 30, (
            f"health_check_interval must be 30s, "
            f"got {kwargs.get('health_check_interval')!r}"
        )
        assert kwargs.get("retry_on_timeout") is True, (
            f"retry_on_timeout must be True, got {kwargs.get('retry_on_timeout')!r}"
        )
        assert kwargs.get("socket_keepalive") is True, (
            f"socket_keepalive must be True, got {kwargs.get('socket_keepalive')!r}"
        )
        # decode_responses must stay False so embeddings/binary payloads
        # survive the round-trip unchanged.
        assert kwargs.get("decode_responses") is False
    finally:
        # Sync close — event loop is not needed since no connection was opened.
        client.connection_pool.disconnect()


def test_redis_client_respects_max_connections() -> None:
    """Caller-supplied max_connections must reach the pool unchanged."""
    client = create_redis_client(
        "redis://localhost:6379/0", max_connections=10
    )
    try:
        assert client.connection_pool.max_connections == 10
    finally:
        client.connection_pool.disconnect()


def test_redis_client_default_max_connections_is_50() -> None:
    """Default pool size defends against accidental silent widening."""
    client = create_redis_client("redis://localhost:6379/0")
    try:
        assert client.connection_pool.max_connections == 50
    finally:
        client.connection_pool.disconnect()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _is_local_redis_up(),
    reason="local Redis not reachable on 127.0.0.1:6379",
)
async def test_redis_client_integration_ping() -> None:
    """Integration smoke — configured client can still PING a real Redis.

    Validates that the resilience kwargs do not break normal operation
    (e.g. a typo'd kwarg would raise TypeError on first command).
    """
    client = create_redis_client("redis://127.0.0.1:6379/0", max_connections=5)
    try:
        pong = await client.ping()
        assert pong is True
    finally:
        await client.aclose()
