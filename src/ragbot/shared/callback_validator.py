"""Validate callback URL by making a test POST."""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)

_BLOCKED_PORTS = frozenset({6379, 5432, 3306, 27017, 9200, 11211})  # Redis, PG, MySQL, Mongo, ES, Memcached

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _check_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if IP is in any blocked range, including IPv4-mapped IPv6."""
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:10.0.0.1 → 10.0.0.1)
    check_ip = ip.ipv4_mapped if hasattr(ip, "ipv4_mapped") and ip.ipv4_mapped else ip
    return any(check_ip in net for net in _BLOCKED_NETWORKS)


async def _is_url_safe(url: str) -> tuple[bool, str]:
    """Check URL doesn't resolve to private/internal IP. Async DNS resolution."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Invalid scheme: {parsed.scheme}"
    port = parsed.port
    if port and port in _BLOCKED_PORTS:
        return False, f"Callback URL uses blocked port: {port}"
    hostname = parsed.hostname
    if not hostname:
        return False, "No hostname in URL"
    try:
        loop = asyncio.get_running_loop()
        addr_info = await loop.getaddrinfo(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return False, f"Cannot resolve hostname: {hostname}"
    for family, *_, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _check_ip_blocked(ip):
            return False, f"URL resolves to blocked IP range: {ip_str}"
    return True, "ok"

CALLBACK_TEST_PAYLOAD = {
    "ok": True,
    "type": "validation",
    "message": "Ragbot callback URL validation. Return 200 to confirm.",
    "expected_response_format": {
        "description": "When ragbot sends real answers, payload will look like this:",
        "success": {
            "ok": True,
            "job_id": "uuid",
            "bot_id": "string",
            "channel_type": "string",
            "connect_id": "string",
            "answer": "câu trả lời từ bot",
            "status": "success",
            "message": "Answer delivered",
        },
        "error": {
            "ok": False,
            "job_id": "uuid",
            "bot_id": "string",
            "channel_type": "string",
            "connect_id": "string",
            "answer": None,
            "status": "error",
            "message": "PIPELINE_TIMEOUT",
        },
    },
}


async def validate_callback_url(url: str, timeout_s: int = 10) -> tuple[bool, str]:
    """POST test payload to callback_url. Returns (ok, message)."""
    # SSRF protection: reject private/internal IPs (async DNS)
    safe, reason = await _is_url_safe(url)
    if not safe:
        logger.warning("callback_url_ssrf_blocked", url=url, reason=reason)
        return False, f"Callback URL rejected: {reason}"
    try:
        async with httpx.AsyncClient(timeout=timeout_s, verify=True) as client:
            resp = await client.post(url, json=CALLBACK_TEST_PAYLOAD)
            if resp.status_code == 200:
                return True, f"Callback URL verified (status={resp.status_code})"
            return False, f"Callback URL returned {resp.status_code}, expected 200"
    except httpx.ConnectError:
        return False, f"Cannot connect to {url}"
    except httpx.TimeoutException:
        return False, f"Callback URL timeout after {timeout_s}s"
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return False, f"Callback URL error: {type(exc).__name__}: {str(exc)[:100]}"
