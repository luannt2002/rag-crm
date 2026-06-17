"""Unit tests for ``scripts.reset_bot_token_quota_monthly.compute_prev_month_key``.

Cron fires 00:01 day-1 (Asia/Ho_Chi_Minh). The key returned is ``YYYY_MM``
of *yesterday* — i.e. the month that just finished — so the JSONB bucket
written into ``bot_token_usage_log.usage_by_month`` is correctly stamped.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from scripts.reset_bot_token_quota_monthly import compute_prev_month_key


class _FakeDatetime(datetime):
    """Stub ``datetime.now(tz)`` for deterministic cron-time tests."""

    _fixed: datetime

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        if tz is None:
            return cls._fixed
        return cls._fixed.astimezone(tz)


def _at(year: int, month: int, day: int, hour: int = 0, minute: int = 1) -> _FakeDatetime:
    from zoneinfo import ZoneInfo

    _FakeDatetime._fixed = datetime(
        year, month, day, hour, minute, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"),
    )
    return _FakeDatetime


def test_compute_prev_month_key_normal():
    """Cron fires 2026-06-01 00:01 ICT → yesterday is 2026-05-31 → key=2026_05."""
    fake = _at(2026, 6, 1)
    with patch("scripts.reset_bot_token_quota_monthly.datetime", fake):
        key = compute_prev_month_key(tz_name="Asia/Ho_Chi_Minh")
    assert key == "2026_05"


def test_compute_prev_month_key_january():
    """Cron fires 2027-01-01 00:01 ICT → yesterday is 2026-12-31 → key=2026_12.

    Year boundary: prev-month wrap across year must produce previous year.
    """
    fake = _at(2027, 1, 1)
    with patch("scripts.reset_bot_token_quota_monthly.datetime", fake):
        key = compute_prev_month_key(tz_name="Asia/Ho_Chi_Minh")
    assert key == "2026_12"


def test_compute_prev_month_key_leap_feb():
    """Cron fires 2024-03-01 00:01 ICT → yesterday is 2024-02-29 (leap) → key=2024_02."""
    fake = _at(2024, 3, 1)
    with patch("scripts.reset_bot_token_quota_monthly.datetime", fake):
        key = compute_prev_month_key(tz_name="Asia/Ho_Chi_Minh")
    assert key == "2024_02"
