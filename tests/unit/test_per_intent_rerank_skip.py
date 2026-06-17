"""T2.S7 — Per-intent rerank skip gate with size-safety.

Lightweight intents (chitchat / greeting / feedback / vu_vo / oos / factoid)
bypass the rerank API call when the candidate pool already fits inside
``rerank_top_n``. Heavyweight intents (multi_hop / aggregation / comparison)
always rerank. Empty skip set disables the gate. Per-bot override flows
through ``resolve_bot_limit`` (threshold_overrides → plan_limits →
system_config → constant) — same chain other plan-limit knobs use.

Coverage:
  1. Skip set pinning vs constants drift.
  2. PLAN_LIMIT_SCHEMA accepts ``rerank_skip_intents`` as ``list_str`` with
     the canonical default; ``validate_plan_limits`` cleans / dedupes.
  3. ``resolve_bot_limit`` honours per-bot ``threshold_overrides`` when set.
  4. Pipeline_config builders (chat_worker + test_chat) forward the value.
  5. Rerank-node integration: skip vs rerank gating across the safety matrix.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.shared.bot_limits import (
    PLAN_LIMIT_SCHEMA,
    resolve_bot_limit,
    validate_plan_limits,
)
from ragbot.shared.constants import (
    DEFAULT_RERANK_SKIP_INTENTS,
    DEFAULT_RERANK_TOP_N,
)


# ---------------------------------------------------------------------------
# 1. Constant pin — protects against silent drift
# ---------------------------------------------------------------------------


def test_default_skip_intents_pin() -> None:
    """Ship-locked set; widening must be a deliberate edit + test update."""
    assert DEFAULT_RERANK_SKIP_INTENTS == frozenset(
        {"chitchat", "oos", "greeting", "feedback", "vu_vo", "factoid"},
    )


def test_default_skip_intents_lowercase_invariant() -> None:
    """Gate compares ``intent.lower()`` — every constant member must be
    already-lowercase to keep the comparison stable."""
    for label in DEFAULT_RERANK_SKIP_INTENTS:
        assert label == label.lower(), f"non-canonical entry: {label!r}"


# ---------------------------------------------------------------------------
# 2. Schema + validate_plan_limits
# ---------------------------------------------------------------------------


def test_plan_limit_schema_has_rerank_skip_intents() -> None:
    schema = PLAN_LIMIT_SCHEMA["rerank_skip_intents"]
    assert schema["type"] == "list_str"
    # Default is a tuple of the canonical lower-case labels.
    default = schema["default"]
    assert set(default) == set(DEFAULT_RERANK_SKIP_INTENTS)


def test_validate_plan_limits_accepts_list() -> None:
    cleaned = validate_plan_limits(
        {"rerank_skip_intents": ["greeting", "chitchat"]},
    )
    # Tuple, deduped, lower-cased, stripped.
    assert cleaned["rerank_skip_intents"] == ("greeting", "chitchat")


def test_validate_plan_limits_normalises_messy_input() -> None:
    cleaned = validate_plan_limits(
        {"rerank_skip_intents": [" Factoid ", "factoid", "", "GREETING"]},
    )
    assert cleaned["rerank_skip_intents"] == ("factoid", "greeting")


def test_validate_plan_limits_rejects_non_list() -> None:
    with pytest.raises(ValueError, match="expected list"):
        validate_plan_limits({"rerank_skip_intents": "factoid"})


# ---------------------------------------------------------------------------
# 3. resolve_bot_limit chain
# ---------------------------------------------------------------------------


def test_resolve_bot_limit_uses_schema_default_when_no_override() -> None:
    bot_cfg = SimpleNamespace(threshold_overrides=None, plan_limits=None)
    resolved = resolve_bot_limit(bot_cfg, "rerank_skip_intents")
    # Schema default propagates as-is (tuple of lower-case labels).
    assert set(resolved) == set(DEFAULT_RERANK_SKIP_INTENTS)


def test_resolve_bot_limit_honours_threshold_override() -> None:
    """Per-bot override shrinks the skip set: only ``greeting`` skips."""
    bot_cfg = SimpleNamespace(
        threshold_overrides={"rerank_skip_intents": ["greeting"]},
        plan_limits=None,
    )
    resolved = resolve_bot_limit(bot_cfg, "rerank_skip_intents")
    assert resolved == ["greeting"]


def test_resolve_bot_limit_uses_system_default_when_provided() -> None:
    bot_cfg = SimpleNamespace(threshold_overrides=None, plan_limits=None)
    resolved = resolve_bot_limit(
        bot_cfg, "rerank_skip_intents",
        system_default=("greeting",),
    )
    assert resolved == ("greeting",)


# ---------------------------------------------------------------------------
# 4. Static-text assertion — both pipeline_config builders forward the key
# ---------------------------------------------------------------------------


def test_chat_worker_forwards_rerank_skip_intents() -> None:
    from pathlib import Path

    # chat_worker was split into a package — scan every module so the
    # forwarded key is found wherever the pipeline_config builder landed.
    pkg = (
        Path(__file__).resolve().parents[2]
        / "src" / "ragbot" / "interfaces" / "workers" / "chat_worker"
    )
    body = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
    )
    assert '"rerank_skip_intents"' in body
    assert "DEFAULT_RERANK_SKIP_INTENTS" in body


def test_test_chat_builder_forwards_rerank_skip_intents() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src" / "ragbot" / "interfaces" / "http" / "routes" / "test_chat"
        / "_pipeline_config.py"
    )
    body = src.read_text(encoding="utf-8")
    assert '"rerank_skip_intents"' in body
    assert "DEFAULT_RERANK_SKIP_INTENTS" in body


# ---------------------------------------------------------------------------
# 5. Rerank-node integration — drives the real closure
# ---------------------------------------------------------------------------


class _CapturingReranker:
    """Reranker port stub recording every ``rerank()`` invocation."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_provider_name(self) -> str:
        return "capturing-fake"

    async def rerank(
        self,
        *,
        query: str,
        chunks: list[dict],
        top_n: int,
        model: str | None = None,
    ) -> list[dict]:
        self.calls.append({
            "query": query,
            "chunks_in": len(chunks),
            "top_n": top_n,
            "model": model,
        })
        out: list[dict] = []
        for c in chunks[:top_n]:
            row = dict(c)
            # Use a high score so the post-rerank min_score floor cannot drop
            # the chunks (active-floor default = 0.30+).
            row["score"] = 0.95
            out.append(row)
        return out


class _RecordingStepCtx:
    def __init__(self) -> None:
        self.metadata: dict = {}

    def set_metadata(self, **kw) -> None:
        self.metadata.update(kw)

    def add_tokens(self, **_kw) -> None:
        pass

    def record_llm(self, **_kw) -> None:
        """Wave M3.2 — no-op mirror of StepContext.record_llm."""
        pass


class _RecordingStepTracker:
    def __init__(self) -> None:
        self.steps: dict[str, _RecordingStepCtx] = {}

    @asynccontextmanager
    async def step(self, name, **_kw):
        ctx = _RecordingStepCtx()
        self.steps[name] = ctx
        yield ctx


async def _run_rerank_node(
    *,
    intent: str,
    skip_intents,
    n_chunks: int,
    rerank_top_n: int,
    reranker: _CapturingReranker,
) -> tuple[dict, _RecordingStepCtx]:
    """Drive the real rerank closure with a forged ``GraphState``."""
    from ragbot.orchestration.query_graph import build_graph

    tracker = _RecordingStepTracker()

    @asynccontextmanager
    async def _noop_invocation(**_kw):
        ctx = MagicMock()
        ctx.record = lambda **_a: None
        yield ctx

    invocation_logger = MagicMock()
    invocation_logger.invoke_model = _noop_invocation

    guardrail = MagicMock()
    guardrail.check_input = AsyncMock(return_value=[])
    guardrail.check_output = AsyncMock(return_value=[])

    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(code="mock")
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": "x", "prompt_tokens": 1, "completion_tokens": 1,
        "cost_usd": 0.0, "finish_reason": "stop",
    })

    graph = build_graph(
        invocation_logger=invocation_logger,
        guardrail=guardrail,
        model_resolver=resolver,
        llm=llm,
        reranker=reranker,
    )

    rerank_node = graph.nodes["rerank"]
    runnable = getattr(rerank_node, "runnable", None) or rerank_node
    bound = getattr(runnable, "bound", None)
    func = bound if bound is not None else runnable
    if hasattr(func, "afunc"):
        func = func.afunc
    elif hasattr(func, "func"):
        func = func.func

    chunks = [
        {"chunk_id": f"c{i}", "content": f"body {i}", "score": 0.5 - i * 0.01}
        for i in range(n_chunks)
    ]

    state: dict = {
        "query": "any query",
        "rewritten_query": None,
        "retrieved_chunks": chunks,
        "intent": intent,
        "pipeline_config": {
            "rerank_top_n": rerank_top_n,
            "reranker_enabled": True,
            "reranker_min_score_active": 0.4,
            "reranker_min_score_bypass": 0.0,
            "rerank_intent_whitelist": None,  # legacy off — isolate the new gate
            "rerank_skip_intents": skip_intents,
        },
        "record_bot_id": uuid4(),
        "step_tracker": tracker,
        "bot_system_prompt": "",
        "kg_service": None,
        "session_factory": None,
    }

    out = await func(state)
    ctx = tracker.steps["rerank"]
    return out, ctx


# -- Skip path: lightweight intent + small pool ------------------------------


def test_factoid_small_pool_skips_rerank() -> None:
    """5 chunks ≤ rerank_top_n=7 → SKIP; reranker NOT called."""
    rk = _CapturingReranker()
    out, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=5,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert rk.calls == [], "reranker must NOT be called when intent in skip set + size safety"
    assert ctx.metadata.get("mode") == "intent_skip_set"
    assert ctx.metadata.get("intent") == "factoid"
    assert ctx.metadata.get("input") == 5
    # Output preserves retrieval order, top_n applied (5 ≤ 7 → all 5 carry).
    assert len(out["reranked_chunks"]) == 5


# -- Safety path: lightweight intent but pool > top_n → still rerank --------


def test_factoid_large_pool_still_reranks() -> None:
    """20 chunks > rerank_top_n=7 → safety triggers, rerank fires."""
    rk = _CapturingReranker()
    out, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=20,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert len(rk.calls) == 1, "reranker must be called when pool > top_n"
    assert ctx.metadata.get("mode") == "rerank"


# -- Heavyweight intent: never skipped --------------------------------------


def test_multi_hop_small_pool_still_reranks() -> None:
    """multi_hop NOT in skip set → rerank even with tiny pool."""
    rk = _CapturingReranker()
    _, ctx = asyncio.run(_run_rerank_node(
        intent="multi_hop",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=5,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"


# -- Existing chitchat behaviour preserved ----------------------------------


def test_chitchat_skips_via_skip_set() -> None:
    """Preserve original chitchat-bypass behaviour. With 100 candidates,
    safety would block skip — but chitchat answers don't care about
    rerank ordering anyway. Test the typical small-pool case (1 chunk)."""
    rk = _CapturingReranker()
    _, ctx = asyncio.run(_run_rerank_node(
        intent="chitchat",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=1,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert rk.calls == []
    assert ctx.metadata.get("mode") == "intent_skip_set"


def test_chitchat_large_pool_still_reranks_under_size_safety() -> None:
    """Documented trade-off: size safety is universal — chitchat with a
    huge pool still pays for one rerank call rather than risk shipping a
    poorly-ordered context. Operators can drop chitchat from the skip set
    via threshold_overrides if they want different behaviour."""
    rk = _CapturingReranker()
    _, ctx = asyncio.run(_run_rerank_node(
        intent="chitchat",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=100,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"


# -- Per-bot override shrinking the skip set --------------------------------


def test_per_bot_override_shrinks_skip_set() -> None:
    """Owner sets ``rerank_skip_intents=["greeting"]`` → factoid drops out
    of the skip set and gets reranked even with a small pool."""
    rk = _CapturingReranker()
    _, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        skip_intents=("greeting",),
        n_chunks=3,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"


def test_per_bot_override_empty_disables_gate() -> None:
    """Empty skip set = gate off; every intent reranks (within other rules)."""
    rk = _CapturingReranker()
    _, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        skip_intents=(),
        n_chunks=3,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"


# -- Edge cases -------------------------------------------------------------


def test_empty_retrieved_chunks_no_crash() -> None:
    """Empty pool short-circuits to ``empty_input`` mode regardless of intent."""
    rk = _CapturingReranker()
    out, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=0,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert rk.calls == []
    assert ctx.metadata.get("mode") == "empty_input"
    assert out["reranked_chunks"] == []


def test_intent_casing_drift_still_skipped() -> None:
    """Classifier may return ``Factoid`` / ``FACTOID`` / ``  factoid  ``;
    the gate compares lower-stripped, so casing drift cannot defeat skip."""
    rk = _CapturingReranker()
    _, ctx = asyncio.run(_run_rerank_node(
        intent="  Factoid  ",
        skip_intents=tuple(sorted(DEFAULT_RERANK_SKIP_INTENTS)),
        n_chunks=3,
        rerank_top_n=DEFAULT_RERANK_TOP_N,
        reranker=rk,
    ))
    assert rk.calls == []
    assert ctx.metadata.get("mode") == "intent_skip_set"
