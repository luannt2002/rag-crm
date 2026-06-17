from __future__ import annotations
from typing import Final  # noqa: F401
from ._22_conversation_state_memory import *  # noqa: F401,F403

# --- CRM analytics read-layer (z-luannt-new-feature.txt) --------------------
# Default lookback window (days) when a CRM endpoint caller omits ``days``.
# 30 gives a month-at-a-glance for the cost/latency/quality dashboard; matches
# the durable monitoring-log default so the two surfaces stay consistent.
DEFAULT_CRM_WINDOW_DAYS: Final[int] = 30
# Hard cap on the lookback window. 365 bounds the percentile_cont GROUP BY scan
# over request_logs / request_steps to one year; anything wider belongs in an
# offline rollup, not a live dashboard query.
MAX_CRM_WINDOW_DAYS: Final[int] = 365
# Default N for the top-expensive-questions endpoint when caller omits ``n``.
DEFAULT_CRM_TOP_N: Final[int] = 10
# Hard cap on top-N. ORDER BY total_tokens DESC LIMIT N sorts the full grouped
# set first, so a malicious large N would force a full-table sort — 50 is the
# masterplan-mandated ceiling that keeps the query bounded.
MAX_CRM_TOP_N: Final[int] = 50
# RBAC level required to read CRM analytics for one tenant (admin = 60).
DEFAULT_CRM_MIN_OPERATOR_LEVEL: Final[int] = 60
# RBAC level required for the cross-tenant CRM view (tenant context absent).
# Only the platform super-admin (100) may aggregate across every tenant.
DEFAULT_CRM_MIN_SUPER_ADMIN_LEVEL: Final[int] = 100
