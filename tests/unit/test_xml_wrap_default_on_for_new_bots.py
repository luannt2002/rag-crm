"""M14 — XML chunk-wrap default ON for new bots.

Resolution chain for ``xml_wrap_enabled``:

1. Explicit per-bot ``plan_limits.xml_wrap_enabled`` → highest priority
   (True / False both honoured — operators can opt prior bots in or new
   bots out).
2. ``bot_created_at >= XML_WRAP_DEFAULT_ON_FROM_DATE`` → default ON.
3. Otherwise → ``DEFAULT_XML_WRAP_ENABLED`` (False) — prior fallback.

These tests pin the helper at module scope so the chunk-wrap behaviour
inside the ``generate`` node stays governed by a single decision point.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ragbot.orchestration.query_graph import _resolve_xml_wrap_enabled
from ragbot.shared.constants import (
    DEFAULT_XML_WRAP_ENABLED,
    XML_WRAP_DEFAULT_ON_FROM_DATE,
)


def _state(*, plan_limits: dict | None = None, created_at: datetime | None = None) -> dict:
    return {
        "pipeline_config": plan_limits or {},
        "bot_created_at": created_at,
    }


# -----------------------------------------------------------------------------
# Layer 1 — explicit per-bot value always wins
# -----------------------------------------------------------------------------


def test_explicit_true_overrides_prior_default() -> None:
    """A prior bot (created before cutoff) with explicit True wraps anyway."""
    prior_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    state = _state(plan_limits={"xml_wrap_enabled": True}, created_at=prior_dt)
    assert _resolve_xml_wrap_enabled(state) is True


def test_explicit_false_overrides_new_bot_default_on() -> None:
    """A new bot can be opted out by setting plan_limits explicitly to False."""
    cutoff = datetime.fromisoformat(XML_WRAP_DEFAULT_ON_FROM_DATE)
    state = _state(plan_limits={"xml_wrap_enabled": False}, created_at=cutoff)
    assert _resolve_xml_wrap_enabled(state) is False


# -----------------------------------------------------------------------------
# Layer 2 — created_at cutoff
# -----------------------------------------------------------------------------


def test_new_bot_defaults_on_when_flag_absent() -> None:
    """Bots created on/after the cutoff default to XML wrap ON."""
    cutoff = datetime.fromisoformat(XML_WRAP_DEFAULT_ON_FROM_DATE)
    state = _state(plan_limits=None, created_at=cutoff)
    assert _resolve_xml_wrap_enabled(state) is True


def test_new_bot_after_cutoff_defaults_on() -> None:
    """One year past cutoff still defaults ON."""
    future_dt = datetime.fromisoformat(XML_WRAP_DEFAULT_ON_FROM_DATE).replace(year=2027)
    state = _state(plan_limits=None, created_at=future_dt)
    assert _resolve_xml_wrap_enabled(state) is True


def test_prior_bot_keeps_off_when_flag_absent() -> None:
    """Legacy bot (created before cutoff) — XML wrap stays OFF (backward-compat)."""
    prior_dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
    state = _state(plan_limits=None, created_at=prior_dt)
    assert _resolve_xml_wrap_enabled(state) is False


def test_tz_aware_created_at_compared_correctly() -> None:
    """``bot_created_at`` arrives tz-aware from DB; the helper must strip tz."""
    cutoff_iso = XML_WRAP_DEFAULT_ON_FROM_DATE
    cutoff = datetime.fromisoformat(cutoff_iso)
    tz_aware = cutoff.replace(tzinfo=timezone.utc)
    state = _state(plan_limits=None, created_at=tz_aware)
    assert _resolve_xml_wrap_enabled(state) is True


# -----------------------------------------------------------------------------
# Layer 3 — fallback when created_at unknown
# -----------------------------------------------------------------------------


def test_missing_created_at_falls_back_to_default_constant() -> None:
    """No timestamp + no explicit flag → SSoT default (currently False)."""
    state = _state(plan_limits=None, created_at=None)
    assert _resolve_xml_wrap_enabled(state) is DEFAULT_XML_WRAP_ENABLED


def test_empty_state_does_not_raise() -> None:
    """Defensive — an empty state must not blow up the resolver."""
    assert _resolve_xml_wrap_enabled({}) is DEFAULT_XML_WRAP_ENABLED
