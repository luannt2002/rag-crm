"""Per-feature aggregator coverage for `scripts/cost_audit.py`.

T2-CostPerf — verify the
``per_feature_aggregate`` pure function:

1. Groups rows by ``feature_name`` and sums tokens + cost.
2. NULL / empty / whitespace-only feature_name collapses to the
   ``unset`` bucket (legacy callers stay visible).
3. Sort order is cost desc → name asc (deterministic for ops dashboards
   and CI snapshots).
4. Heterogeneous ``cost_usd`` input (Decimal, str, float, int) all
   sum correctly — model_invocations.cost_usd is NUMERIC(12,6) which
   psycopg2 surfaces as Decimal.
5. Malformed cost values are skipped, not crashed on — observability
   MUST never break callers.
6. Empty input → empty output (no synthetic zero row).

Why test the pure aggregator rather than the DB path: the DB read is a
thin psycopg2 fetch (``_fetch_per_feature_rows``); injecting canned
rows exercises the aggregation logic that actually carries cost-audit
correctness — and stays runnable without a live Postgres.
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest


def _load_cost_audit() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "cost_audit.py"
    assert script_path.exists(), f"cost_audit script missing: {script_path}"
    spec = importlib.util.spec_from_file_location("_cost_audit_pf", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cost_audit() -> ModuleType:
    return _load_cost_audit()


# ---------------------------------------------------------------------------
# Test 1 — basic grouping + sum: two features, three rows.
# ---------------------------------------------------------------------------


def test_aggregate_groups_by_feature(cost_audit: ModuleType) -> None:
    rows = [
        {"feature_name": "query.generation",
         "prompt_tokens": 100, "completion_tokens": 50, "cost_usd": 0.01},
        {"feature_name": "query.generation",
         "prompt_tokens": 200, "completion_tokens": 75, "cost_usd": 0.02},
        {"feature_name": "ingest.enrich",
         "prompt_tokens": 50, "completion_tokens": 25, "cost_usd": 0.005},
    ]
    out = cost_audit.per_feature_aggregate(rows)

    by_name = {r["feature_name"]: r for r in out}
    assert set(by_name.keys()) == {"query.generation", "ingest.enrich"}

    qg = by_name["query.generation"]
    assert qg["calls"] == 2
    assert qg["prompt_tokens"] == 300
    assert qg["completion_tokens"] == 125
    assert qg["total_tokens"] == 425
    assert qg["cost_usd"] == pytest.approx(0.03)

    ie = by_name["ingest.enrich"]
    assert ie["calls"] == 1
    assert ie["prompt_tokens"] == 50
    assert ie["completion_tokens"] == 25
    assert ie["total_tokens"] == 75
    assert ie["cost_usd"] == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# Test 2 — NULL / empty / whitespace feature_name → unset bucket.
# ---------------------------------------------------------------------------


def test_aggregate_null_and_empty_to_unset(cost_audit: ModuleType) -> None:
    rows = [
        {"feature_name": None, "prompt_tokens": 10,
         "completion_tokens": 5, "cost_usd": 0.001},
        {"feature_name": "", "prompt_tokens": 20,
         "completion_tokens": 10, "cost_usd": 0.002},
        {"feature_name": "   ", "prompt_tokens": 30,
         "completion_tokens": 15, "cost_usd": 0.003},
        {"feature_name": "query.rewrite", "prompt_tokens": 5,
         "completion_tokens": 2, "cost_usd": 0.0005},
    ]
    out = cost_audit.per_feature_aggregate(rows)
    by_name = {r["feature_name"]: r for r in out}

    assert "unset" in by_name, "NULL/empty/whitespace must collapse to unset"
    unset = by_name["unset"]
    assert unset["calls"] == 3
    assert unset["prompt_tokens"] == 60
    assert unset["completion_tokens"] == 30
    assert unset["cost_usd"] == pytest.approx(0.006)

    # Non-empty name kept untouched.
    assert "query.rewrite" in by_name
    assert by_name["query.rewrite"]["calls"] == 1

    # Sentinel matches the module constant — pin so a future rename of
    # the bucket name forces an explicit migration of dashboards.
    assert cost_audit.FEATURE_NAME_UNSET_BUCKET == "unset"


# ---------------------------------------------------------------------------
# Test 3 — sort by cost desc, then name asc on ties.
# ---------------------------------------------------------------------------


def test_aggregate_sort_cost_desc_then_name(cost_audit: ModuleType) -> None:
    rows = [
        {"feature_name": "alpha", "prompt_tokens": 1,
         "completion_tokens": 1, "cost_usd": 0.001},
        {"feature_name": "bravo", "prompt_tokens": 1,
         "completion_tokens": 1, "cost_usd": 0.005},
        {"feature_name": "charlie", "prompt_tokens": 1,
         "completion_tokens": 1, "cost_usd": 0.005},  # ties bravo
        {"feature_name": "delta", "prompt_tokens": 1,
         "completion_tokens": 1, "cost_usd": 0.002},
    ]
    out = cost_audit.per_feature_aggregate(rows)
    names = [r["feature_name"] for r in out]

    # cost desc: bravo(0.005) = charlie(0.005) > delta(0.002) > alpha(0.001)
    # name asc on tie: bravo < charlie
    assert names == ["bravo", "charlie", "delta", "alpha"]


# ---------------------------------------------------------------------------
# Test 4 — heterogeneous cost types all sum correctly.
# ---------------------------------------------------------------------------


def test_aggregate_decimal_and_string_cost(cost_audit: ModuleType) -> None:
    # psycopg2 returns NUMERIC as Decimal; RealDictCursor preserves that.
    # Some legacy callers may stringify before pickling — accept both.
    rows = [
        {"feature_name": "ingest.embed",
         "prompt_tokens": 100, "completion_tokens": 0,
         "cost_usd": Decimal("0.012345")},
        {"feature_name": "ingest.embed",
         "prompt_tokens": 50, "completion_tokens": 0,
         "cost_usd": "0.000005"},
        {"feature_name": "ingest.embed",
         "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 1},
        {"feature_name": "ingest.embed",
         "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 2.5},
    ]
    out = cost_audit.per_feature_aggregate(rows)
    assert len(out) == 1
    r = out[0]
    assert r["feature_name"] == "ingest.embed"
    assert r["calls"] == 4
    assert r["cost_usd"] == pytest.approx(0.012345 + 0.000005 + 1 + 2.5)


# ---------------------------------------------------------------------------
# Test 5 — malformed cost / missing fields don't crash.
# ---------------------------------------------------------------------------


def test_aggregate_malformed_cost_skipped(cost_audit: ModuleType) -> None:
    rows = [
        {"feature_name": "router.classify",
         "prompt_tokens": 10, "completion_tokens": 5,
         "cost_usd": "not-a-number"},  # malformed → skipped, NOT raised
        {"feature_name": "router.classify",
         # Missing prompt/completion — default to 0.
         "cost_usd": 0.001},
        {"feature_name": "router.classify",
         "prompt_tokens": None, "completion_tokens": None,
         "cost_usd": None},  # all None → 0
    ]
    out = cost_audit.per_feature_aggregate(rows)
    assert len(out) == 1
    r = out[0]
    assert r["calls"] == 3
    assert r["prompt_tokens"] == 10
    assert r["completion_tokens"] == 5
    # Only the valid 0.001 row contributes cost.
    assert r["cost_usd"] == pytest.approx(0.001)


# ---------------------------------------------------------------------------
# Test 6 — empty input → empty output.
# ---------------------------------------------------------------------------


def test_aggregate_empty_input(cost_audit: ModuleType) -> None:
    assert cost_audit.per_feature_aggregate([]) == []


# ---------------------------------------------------------------------------
# Test 7 — argparse wires `per-feature` with sane defaults.
# ---------------------------------------------------------------------------


def test_per_feature_subcommand_registered(cost_audit: ModuleType) -> None:
    # Pinning the subcommand wiring + defaults guards against an
    # accidental refactor dropping the new subparser entirely.
    assert callable(cost_audit.cmd_per_feature)
    assert cost_audit.DEFAULT_PER_FEATURE_WINDOW_DAYS > 0
    assert cost_audit.DEFAULT_PER_FEATURE_TOP > 0


# ---------------------------------------------------------------------------
# Test 8 — feature_name truncated whitespace is treated as the name itself.
# ---------------------------------------------------------------------------


def test_aggregate_strips_outer_whitespace(cost_audit: ModuleType) -> None:
    rows = [
        {"feature_name": "  query.generation  ",
         "prompt_tokens": 1, "completion_tokens": 0, "cost_usd": 0.0001},
        {"feature_name": "query.generation",
         "prompt_tokens": 1, "completion_tokens": 0, "cost_usd": 0.0001},
    ]
    out = cost_audit.per_feature_aggregate(rows)
    # Both should collapse to the same trimmed bucket.
    assert len(out) == 1
    assert out[0]["feature_name"] == "query.generation"
    assert out[0]["calls"] == 2


# ---------------------------------------------------------------------------
# Tests 9-13 — source-level assertions for the producer side of the
# per-feature pipeline. We assert against the LOCAL worktree's source
# files rather than the importable ragbot package: parallel coder agents
# share the dev venv via an editable-install ``.pth`` that may point at
# a sibling worktree, so an ``import ragbot`` here would silently
# read the wrong file. Source-level checks are cross-worktree-safe and
# fast (no SQLAlchemy roundtrip), and they are sufficient to catch a
# refactor that drops the kwarg / column / constant.
# ---------------------------------------------------------------------------


def test_invocation_logger_signature_has_feature_name() -> None:
    """``InvocationLogger.invoke_model`` exposes a ``feature_name`` kwarg.

    Smoke test: catches a regression where someone drops the kwarg from
    the public API. Cross-worktree-safe because it shells out to the
    local source file, not the venv-installed copy.
    """
    import inspect

    local_path = (Path(__file__).resolve().parents[2]
                  / "src/ragbot/infrastructure/observability/invocation_logger.py")
    src = local_path.read_text()
    # We grep the public signature directly — robust against the editable
    # install of a sibling worktree masking this worktree's source.
    assert "feature_name: str | None = None" in src, (
        "InvocationLogger.invoke_model must expose feature_name kwarg"
    )


def test_model_invocations_orm_has_feature_name_column() -> None:
    """``model_invocations.feature_name`` is declared on the ORM model.

    Source-level assertion — bypasses any editable-install ambiguity.
    """
    src = (Path(__file__).resolve().parents[2]
           / "src/ragbot/infrastructure/db/models_invocation.py").read_text()
    assert "feature_name: Mapped[str | None]" in src
    assert "FEATURE_NAME_MAX_LEN" in src


def test_constants_declare_feature_name_limits() -> None:
    """Zero-hardcode: FEATURE_NAME_MAX_LEN lives in the shared/constants package."""
    pkg = Path(__file__).resolve().parents[2] / "src/ragbot/shared/constants"
    src = "\n".join(p.read_text() for p in pkg.glob("*.py"))
    assert "FEATURE_NAME_MAX_LEN: Final[int] = 64" in src
    assert "DEFAULT_FEATURE_NAME_UNSET: Final[str]" in src


def test_alembic_migration_adds_feature_name() -> None:
    """Migration adds the ``feature_name`` column + index.

    Originally landed as ``0094`` alongside another ``0094`` from a parallel
    track; relabelled to ``0094a`` (down_revision ``0094``) during the
    multi-head consolidation so the chain stays linear.
    """
    mig = (Path(__file__).resolve().parents[2]
           / "alembic/versions/20260514_0094a_add_feature_name_to_model_invocations.py")
    assert mig.exists()
    src = mig.read_text()
    assert 'revision = "0094a"' in src
    assert 'down_revision = "0094"' in src
    assert "ADD COLUMN IF NOT EXISTS feature_name VARCHAR(64)" in src
    assert "ix_model_inv_feature_started" in src
    # Downgrade must drop both — guards against half-rollback bugs.
    assert "DROP INDEX IF EXISTS ix_model_inv_feature_started" in src
    assert "DROP COLUMN IF EXISTS feature_name" in src


def test_query_graph_threads_feature_name() -> None:
    """Orchestration callsites pass ``query.<purpose>`` as feature_name.

    Confirms the producer side: cost audit per-feature output won't be
    100% ``unset`` after this stream lands.
    """
    src = (Path(__file__).resolve().parents[2]
           / "src/ragbot/orchestration/query_graph.py").read_text()
    # Both _invoke_llm_node and the structured-output sibling must thread it.
    assert src.count('feature_name=feature_name') >= 2
    assert 'feature_name = f"query.{purpose}"' in src
