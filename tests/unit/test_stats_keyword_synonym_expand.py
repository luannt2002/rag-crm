"""Pin the stats-route synonym OR-expansion (H2 list-coverage fix).

The structured LIST route used a single raw ``ILIKE '%kw%'`` so a generic
keyword ("da") missed owner-named siblings ("da chết", "chăm sóc da").
``query_by_name_keyword`` now OR-expands a per-bot synonym list (bound
params). The query_graph helper resolves that list from
``bot_custom_vocabulary["synonyms"]``. Both are domain-neutral.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest

from ragbot.infrastructure.repositories.stats_index_repository import (
    StatsIndexRepository,
)
from ragbot.orchestration.query_graph import _resolve_stats_keyword_synonyms


# ── helper resolution (pure) ────────────────────────────────────────────────
def _state_with_synonyms(mapping):
    return {"pipeline_config": {"bot_custom_vocabulary": {"synonyms": mapping}}}


def test_helper_resolves_owner_synonyms():
    st = _state_with_synonyms({"da": ["da chết", "chăm sóc da", "trẻ hóa da"]})
    assert _resolve_stats_keyword_synonyms(st, "da") == [
        "da chết", "chăm sóc da", "trẻ hóa da",
    ]


def test_helper_case_insensitive_key_match():
    st = _state_with_synonyms({"Da": ["da chết"]})
    assert _resolve_stats_keyword_synonyms(st, "DA") == ["da chết"]


def test_helper_no_vocab_returns_empty():
    assert _resolve_stats_keyword_synonyms({"pipeline_config": {}}, "da") == []
    assert _resolve_stats_keyword_synonyms(_state_with_synonyms({}), "xe") == []


# ── repo OR-expansion (captures SQL + bound params) ─────────────────────────
class _FakeResult:
    def fetchall(self):
        return []


class _FakeSession:
    def __init__(self, sink):
        self._sink = sink

    async def execute(self, stmt, params):
        # Capture the FORWARD query (first call). When it returns no rows the
        # repo runs a reverse/token fallback (2026-06-20 q12 fix) as a 2nd
        # execute — these tests assert on the forward synonym expansion, so we
        # keep the first call and ignore the fallback's overwrite.
        if "sql" not in self._sink:
            self._sink["sql"] = str(stmt)
            self._sink["params"] = params
        return _FakeResult()


def _fake_sf(sink):
    @asynccontextmanager
    async def _cm():
        yield _FakeSession(sink)

    return _cm


@pytest.mark.asyncio
async def test_repo_or_expands_synonyms_as_bound_params():
    sink: dict = {}
    repo = StatsIndexRepository(session_factory=_fake_sf(sink))
    await repo.query_by_name_keyword(
        record_bot_id=uuid.uuid4(),
        keyword="da",
        synonyms=["da chết", "chăm sóc da"],
    )
    sql = sink["sql"]
    params = sink["params"]
    # one bound param per de-duplicated variant (raw + 2 synonyms = 3)
    assert params["kw0"] == "%da%"
    assert params["kw1"] == "%da chết%"
    assert params["kw2"] == "%chăm sóc da%"
    # OR-joined, and values are bound (never interpolated) → injection-safe
    assert sql.count(" OR ") >= 2
    assert "da chết" not in sql  # value lives in params, not the SQL string


@pytest.mark.asyncio
async def test_repo_dedups_and_falls_back_to_raw_keyword():
    sink: dict = {}
    repo = StatsIndexRepository(session_factory=_fake_sf(sink))
    # duplicate synonym (case-variant) collapses; None synonyms → raw only
    await repo.query_by_name_keyword(
        record_bot_id=uuid.uuid4(), keyword="da", synonyms=["DA", "da"],
    )
    assert sink["params"]["kw0"] == "%da%"
    assert "kw1" not in sink["params"], "case/exact dupes must collapse"

    sink2: dict = {}
    repo2 = StatsIndexRepository(session_factory=_fake_sf(sink2))
    await repo2.query_by_name_keyword(record_bot_id=uuid.uuid4(), keyword="xe")
    assert sink2["params"]["kw0"] == "%xe%"
    assert "kw1" not in sink2["params"], "no synonyms → behaviour unchanged"
