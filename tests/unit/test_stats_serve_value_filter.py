"""Step-2 (truth-audit option (b)): customer-facing stats queries must be able to
exclude SHELL entities — rows with no price AND no value-bearing attribute.

Baseline evidence (specs/001-rag-truth-audit/evidence/baseline_report.md): serving
shell rows next to a priced same-size sibling produced 45/45 wrong-brand price
answers (P-02/03/04). The filter is schema-keyed (price columns + attributes_json
key shapes) — NO bot/brand literal anywhere (platform-neutral, CLAUDE.md).

NUANCE the filter MUST preserve: a price-less row whose attributes carry a real
VALUE field (e.g. an arrival-date column "28-thg 11") is NOT a shell — date
questions are answered from such rows. Only identity-attrs-only rows are shells.
"""
from __future__ import annotations

import asyncio
import uuid

from ragbot.infrastructure.repositories.stats_index_repository import (
    STATS_NON_VALUE_ATTR_KEYS,
    StatsIndexRepository,
    _value_bearing_predicate,
)


# ---------------------------------------------------------------------------
# SQL-shape tests on the pure helper (mirrors test_stats_price_range_clause.py)
# ---------------------------------------------------------------------------

def test_predicate_accepts_price_or_value_attr() -> None:
    sql = _value_bearing_predicate()
    # A price on either column keeps the row.
    assert "price_primary IS NOT NULL" in sql
    assert "price_secondary IS NOT NULL" in sql
    # Or any attribute whose KEY is not identity/internal and value non-blank.
    assert "jsonb_each_text" in sql
    assert " OR " in sql


def test_predicate_excludes_identity_keys_only_via_constant() -> None:
    """The identity-key list is the SSoT constant — the SQL must bind/inline every
    key from it, so formatter & filter can never drift apart."""
    sql = _value_bearing_predicate()
    for k in STATS_NON_VALUE_ATTR_KEYS:
        assert k in sql, f"identity key {k!r} missing from predicate"
    # The known identity keys (shape of the real shell rows in evidence/):
    for required in ("question", "productname", "answer", "image", "chunk_index", "variants"):
        assert required in STATS_NON_VALUE_ATTR_KEYS


# ---------------------------------------------------------------------------
# Method-level: require_value must gate the predicate into the 3 customer paths
# ---------------------------------------------------------------------------

class _FakeResult:
    def fetchall(self):  # noqa: D401
        return []

    def fetchone(self):
        return (0,)


class _FakeSession:
    def __init__(self, log: list):
        self._log = log

    async def execute(self, stmt, params=None):
        self._log.append((str(stmt), params or {}))
        return _FakeResult()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _repo_with_log() -> tuple[StatsIndexRepository, list]:
    log: list = []

    def factory():
        return _FakeSession(log)

    return StatsIndexRepository(session_factory=factory), log


def _run(coro):
    return asyncio.run(coro)


def test_query_by_name_keyword_applies_filter_only_when_asked() -> None:
    repo, log = _repo_with_log()
    bid = uuid.uuid4()
    _run(repo.query_by_name_keyword(record_bot_id=bid, keyword="abc", require_value=True))
    sql_on = log[-1][0]
    assert "jsonb_each_text" in sql_on, "require_value=True must add the value predicate"

    _run(repo.query_by_name_keyword(record_bot_id=bid, keyword="abc"))
    sql_off = log[-1][0]
    assert "jsonb_each_text" not in sql_off, "default must be unchanged behavior"


def test_list_all_entities_applies_filter_only_when_asked() -> None:
    repo, log = _repo_with_log()
    bid = uuid.uuid4()
    _run(repo.list_all_entities(record_bot_id=bid, require_value=True))
    assert "jsonb_each_text" in log[-1][0]
    _run(repo.list_all_entities(record_bot_id=bid))
    assert "jsonb_each_text" not in log[-1][0]


def test_count_by_name_keyword_applies_filter_only_when_asked() -> None:
    repo, log = _repo_with_log()
    bid = uuid.uuid4()
    _run(repo.count_by_name_keyword(record_bot_id=bid, keyword="abc", require_value=True))
    assert "jsonb_each_text" in log[-1][0]
    _run(repo.count_by_name_keyword(record_bot_id=bid, keyword="abc"))
    assert "jsonb_each_text" not in log[-1][0]


# ---------------------------------------------------------------------------
# Config plumbing: per-bot knob exists with platform default ON (owner decision b)
# ---------------------------------------------------------------------------

def test_plan_limit_knob_and_constant() -> None:
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA
    from ragbot.shared.constants import DEFAULT_STATS_SERVE_REQUIRE_VALUE

    assert DEFAULT_STATS_SERVE_REQUIRE_VALUE is True  # decision record: option (b) default ON
    knob = PLAN_LIMIT_SCHEMA["stats_serve_require_value"]
    assert knob["type"] == "bool"
    assert knob["default"] is DEFAULT_STATS_SERVE_REQUIRE_VALUE
