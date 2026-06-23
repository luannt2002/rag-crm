"""Conversation-state memory defaults — slot-fill persistence guards.

Action-state (slot-fill booking/lead memory) lives in
``conversations.action_state`` (JSONB). These constants bound it so the
LLM slot-extractor cannot bloat the blob with many/garbage keys and so a
stale half-flow self-clears.

SSoT: defaults declared here; runtime override via ``system_config``
(``conversation_state_ttl_hours``) and per-bot ``plan_limits``.
"""

from __future__ import annotations

from typing import Final

# Hours of inactivity (since ``conversations.last_message_at``) after which
# action-state is treated as expired → loaded as empty (flow resets).
DEFAULT_CONVERSATION_STATE_TTL_HOURS: Final[int] = 24

# Max number of slot fields. Enforced in TWO places:
#  1. Admin API validation — the owner-submitted ``action_config.slots_schema``
#     from the FE may declare at most this many fields per action (reject >5).
#  2. Runtime save — ``action_state.slots_filled`` is capped to this many keys
#     (defensive: extractor must not bloat the blob with fabricated keys).
DEFAULT_MAX_ACTION_SLOTS: Final[int] = 5

# Allowed top-level keys in the persisted action_state blob. Anything else
# is dropped on save (anti-garbage). Runtime-only keys (e.g. drift severity)
# are injected by the orchestrator and never persisted.
ACTION_STATE_ALLOWED_TOP_KEYS: Final[frozenset[str]] = frozenset(
    {"intent", "slots_filled", "service_locked"}
)

# Char window for a service-name token harvested from a CSV-shaped answer line
# (drift candidate). Below the floor = an ordinal/code fragment; above the cap =
# a prose blob, not a name. Shape-only, domain-neutral.
DEFAULT_SERVICE_NAME_MIN_CHARS: Final[int] = 3
DEFAULT_SERVICE_NAME_MAX_CHARS: Final[int] = 80

# The "k" unit = one thousand. A bare number below it carrying a "k" suffix
# ("199k") is thousands-shorthand → ×1000; at or above it the number is already
# absolute so the suffix is ignored. Same value plays BOTH roles (threshold +
# multiplier) because it is literally the kilo unit. Price floor/ceiling come from
# the canonical DEFAULT_PRICE_MIN_VND / DEFAULT_PRICE_MAX_VND (no tenant-tuned window).
DEFAULT_K_SUFFIX_MULTIPLIER: Final[int] = 1000
