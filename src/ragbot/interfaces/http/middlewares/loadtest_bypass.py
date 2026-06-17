"""Loadtest bypass helper — env-token-gated, localhost-only.

A request earns the bypass iff three independent gates hold:

1. The env var :pydata:`RAGBOT_LOADTEST_BYPASS_ENV` is set to a non-empty
   value. Production deployments never set it; absence keeps the bypass
   off by default (fail-closed).
2. The header :pydata:`RAGBOT_LOADTEST_BYPASS_HEADER` matches the env
   value under :pyfunc:`secrets.compare_digest` — constant-time compare
   defends against timing oracles harvesting the token.
3. The connection peer is loopback (``127.0.0.1`` / ``::1``) — even if
   a token leaks, a remote attacker cannot exercise it.

Bypass scope: anti-abuse 4xx-ratio counter + IP rate-limit cap.
Auth, RBAC, UA denylist, and honeypot routes still apply.

The helper lives in its own module so both
:pymod:`ragbot.interfaces.http.middlewares.anti_abuse` and
:pymod:`ragbot.interfaces.http.middlewares.ip_rate_limit` can import it
without re-introducing the circular dependency the original layering
already navigates around.
"""

from __future__ import annotations

import os
import secrets

import structlog
from starlette.requests import Request

from ragbot.shared.constants import (
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

logger = structlog.get_logger(__name__)

# IPv4 + IPv6 loopback peer addresses. Bypass is keyed to local-origin
# requests so a public attacker who learns the token still can't burn it.
LOADTEST_BYPASS_LOCALHOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1"})


def is_loadtest_bypass(request: Request) -> bool:
    """True iff the caller presents the operator-issued loadtest token.

    Fail-closed on every gate: missing env, missing/mismatched header, or
    non-loopback peer all return False without logging (the failure mode
    must not leak whether the env is configured).
    """
    env_value = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    if not env_value:
        return False
    header_value = request.headers.get(RAGBOT_LOADTEST_BYPASS_HEADER, "")
    if not header_value:
        return False
    peer = (request.client.host if request.client else "") or ""
    if peer not in LOADTEST_BYPASS_LOCALHOSTS:
        return False
    if not secrets.compare_digest(header_value, env_value):
        return False
    logger.info("loadtest_bypass_used", path=request.url.path, peer=peer)
    return True


__all__ = ["LOADTEST_BYPASS_LOCALHOSTS", "is_loadtest_bypass"]
