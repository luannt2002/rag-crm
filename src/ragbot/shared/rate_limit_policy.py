"""Per-endpoint rate-limit policy table.

The middleware (``interfaces/http/middlewares/rate_limit.py``) reads
this table to map a request path to ``(limit, window_s, burst_factor,
burst_window_s)``. Patterns are matched in declaration order; first
match wins. ``None`` means the endpoint is unlimited (e.g. health
probes the platform must keep responsive at all times).

Domain-neutral
--------------
Policy keys are URL prefixes / regex patterns of the platform's HTTP
contract — no tenant / brand literal. Operator overrides (per-tenant
exceptions) belong in ``tenants.config.rate_limit_overrides`` JSONB
(future work).

Zero-hardcode
-------------
Numeric values land in ``shared/constants.py`` so ops can tune via env
or DB without code edits. The table is a tuple of pattern-tuples; we
use ``re.fullmatch`` so each entry must explicitly anchor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ragbot.shared.constants import (
    DEFAULT_RL_ADMIN_PER_MIN,
    DEFAULT_RL_BURST_FACTOR,
    DEFAULT_RL_BURST_WINDOW_S,
    DEFAULT_RL_CHAT_PER_MIN,
    DEFAULT_RL_DEFAULT_PER_MIN,
    DEFAULT_RL_SYNC_PER_MIN,
    DEFAULT_RL_WINDOW_S,
)


@dataclass(slots=True, frozen=True)
class RateLimitPolicy:
    """Effective per-endpoint policy.

    ``limit=0`` = soft-unlimited (counter still maintained; never 429).
    ``burst_factor=1.0`` = burst disabled (steady-state only).
    """

    limit: int
    window_s: int
    burst_factor: float
    burst_window_s: int


_UNLIMITED_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"/health(/.*)?"),
    re.compile(r"/metrics"),
    re.compile(r"/openapi.*"),
    re.compile(r"/docs(/.*)?"),
    re.compile(r"/redoc(/.*)?"),
    re.compile(r"/static/.*"),
    re.compile(r"/demo-ragbot(/.*)?"),
)


# Policy table — first match wins. Patterns are full regex anchored at
# both ends via ``re.fullmatch``. Adding a route = append a tuple.
_POLICY_TABLE: Final[tuple[tuple[re.Pattern[str], RateLimitPolicy], ...]] = (
    (
        re.compile(r"/api/ragbot/test/chat(/.*)?"),
        RateLimitPolicy(
            limit=DEFAULT_RL_CHAT_PER_MIN,
            window_s=DEFAULT_RL_WINDOW_S,
            burst_factor=DEFAULT_RL_BURST_FACTOR,
            burst_window_s=DEFAULT_RL_BURST_WINDOW_S,
        ),
    ),
    (
        re.compile(r"/api/ragbot/chat(/.*)?"),
        RateLimitPolicy(
            limit=DEFAULT_RL_CHAT_PER_MIN,
            window_s=DEFAULT_RL_WINDOW_S,
            burst_factor=DEFAULT_RL_BURST_FACTOR,
            burst_window_s=DEFAULT_RL_BURST_WINDOW_S,
        ),
    ),
    (
        re.compile(r"/api/ragbot/admin/.*"),
        RateLimitPolicy(
            limit=DEFAULT_RL_ADMIN_PER_MIN,
            window_s=DEFAULT_RL_WINDOW_S,
            burst_factor=1.0,
            burst_window_s=0,
        ),
    ),
    (
        re.compile(r"/api/ragbot/sync/.*"),
        RateLimitPolicy(
            limit=DEFAULT_RL_SYNC_PER_MIN,
            window_s=DEFAULT_RL_WINDOW_S,
            burst_factor=1.0,
            burst_window_s=0,
        ),
    ),
)


_DEFAULT_POLICY: Final[RateLimitPolicy] = RateLimitPolicy(
    limit=DEFAULT_RL_DEFAULT_PER_MIN,
    window_s=DEFAULT_RL_WINDOW_S,
    burst_factor=DEFAULT_RL_BURST_FACTOR,
    burst_window_s=DEFAULT_RL_BURST_WINDOW_S,
)


def resolve_policy(path: str) -> RateLimitPolicy | None:
    """Return the policy that applies to ``path``.

    Returns ``None`` ONLY for unlimited paths (``/health``, ``/metrics``).
    Every other path matches either an explicit table entry or the
    fallback :data:`_DEFAULT_POLICY` so the middleware always has a
    decision to make.
    """
    for pat in _UNLIMITED_PATTERNS:
        if pat.fullmatch(path):
            return None
    for pat, policy in _POLICY_TABLE:
        if pat.fullmatch(path):
            return policy
    return _DEFAULT_POLICY


def list_policies() -> tuple[tuple[str, RateLimitPolicy | None], ...]:
    """Render the policy table (pattern source, policy) for diagnostics."""
    rows: list[tuple[str, RateLimitPolicy | None]] = []
    for pat in _UNLIMITED_PATTERNS:
        rows.append((pat.pattern, None))
    for pat, policy in _POLICY_TABLE:
        rows.append((pat.pattern, policy))
    rows.append(("<default>", _DEFAULT_POLICY))
    return tuple(rows)


__all__ = (
    "RateLimitPolicy",
    "list_policies",
    "resolve_policy",
)
