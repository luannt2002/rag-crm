"""Roll-up query-layer gate for TokenLedgerAnalyticsRepository (G-3).

The repo previously had only ``usage_timeseries``. These tests pin the new
``usage_rollup`` (per-bot/workspace/tenant Σ tokens/cost + bot_count/turns) and
``cross_tenant_rollup`` (platform leaderboard, NO tenant filter) without a live
DB — a fake session captures the compiled SQL + bound params and serves canned
mapping rows so we assert:

  * tenant-scoped rollup binds the caller's tenant + window and groups by the
    requested dimension column;
  * the ``purpose`` breakdown adds a second GROUP BY key (the per-purpose
    attribution payoff);
  * a bad breakdown key falls back to 'none' (closed whitelist, injection-safe);
  * cross_tenant_rollup binds NO tenant filter and bounds by LIMIT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest

from ragbot.infrastructure.repositories.token_ledger_analytics_repository import (
    TokenLedgerAnalyticsRepository,
)


class _FakeMappings:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.last_sql: str = ""
        self.last_params: dict[str, Any] = {}

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, sql: Any, params: dict[str, Any]) -> _FakeResult:
        self.last_sql = str(sql)
        self.last_params = dict(params)
        return _FakeResult(self._rows)


def _make_repo(rows: list[dict[str, Any]]) -> tuple[TokenLedgerAnalyticsRepository, _FakeSession]:
    session = _FakeSession(rows)

    def _factory() -> _FakeSession:
        return session

    return TokenLedgerAnalyticsRepository(session_factory=_factory), session  # type: ignore[arg-type]


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - timedelta(days=7), now


@pytest.mark.asyncio
async def test_usage_rollup_by_bot_binds_tenant_and_groups_by_bot():
    tenant = uuid4()
    seeded = [
        {"dim_key": uuid4(), "bot_id": "support", "bot_count": 1,
         "workspace_count": 1, "turns": 12, "tokens_in": 1000,
         "tokens_out": 200, "tokens_total": 1200, "cost_usd": 0.05, "calls": 20},
    ]
    repo, session = _make_repo(seeded)
    date_from, date_to = _window()
    out = await repo.usage_rollup(
        record_tenant_id=tenant, date_from=date_from, date_to=date_to, dim="bot",
    )
    assert out == seeded
    # tenant + window bound as params (not interpolated → injection-safe).
    assert session.last_params["tenant"] == tenant
    assert session.last_params["date_from"] == date_from
    assert session.last_params["date_to"] == date_to
    # grouped by the bot dimension column + filtered by tenant.
    assert "record_tenant_id = :tenant" in session.last_sql
    assert "GROUP BY record_bot_id" in session.last_sql
    assert "count(DISTINCT request_id)" in session.last_sql  # turns


@pytest.mark.asyncio
async def test_usage_rollup_workspace_dim_groups_by_workspace():
    repo, session = _make_repo([])
    date_from, date_to = _window()
    await repo.usage_rollup(
        record_tenant_id=uuid4(), date_from=date_from, date_to=date_to, dim="workspace",
    )
    assert "GROUP BY workspace_id" in session.last_sql


@pytest.mark.asyncio
async def test_usage_rollup_purpose_breakdown_adds_second_group_key():
    repo, session = _make_repo([])
    date_from, date_to = _window()
    await repo.usage_rollup(
        record_tenant_id=uuid4(), date_from=date_from, date_to=date_to,
        dim="bot", breakdown="purpose",
    )
    assert "purpose AS breakdown_key" in session.last_sql
    assert ", purpose" in session.last_sql


@pytest.mark.asyncio
async def test_usage_rollup_bad_breakdown_falls_back_to_none():
    repo, session = _make_repo([])
    date_from, date_to = _window()
    await repo.usage_rollup(
        record_tenant_id=uuid4(), date_from=date_from, date_to=date_to,
        dim="bot", breakdown="DROP TABLE token_ledger; --",
    )
    # Injection attempt is NOT interpolated — falls back to a NULL breakdown key.
    assert "DROP TABLE" not in session.last_sql
    assert "NULL AS breakdown_key" in session.last_sql


@pytest.mark.asyncio
async def test_cross_tenant_rollup_has_no_tenant_filter_and_binds_limit():
    repo, session = _make_repo([])
    date_from, date_to = _window()
    await repo.cross_tenant_rollup(date_from=date_from, date_to=date_to, limit=25)
    # NO tenant scoping — platform-wide leaderboard.
    assert ":tenant" not in session.last_sql
    assert "record_tenant_id = " not in session.last_sql
    assert "GROUP BY record_tenant_id" in session.last_sql
    assert "count(DISTINCT workspace_id)" in session.last_sql
    assert session.last_params["lim"] == 25
