"""Phase 14 — per-bot rerank intent whitelist gate.

Covers:
  1. ``RerankIntentWhitelist`` Pydantic model — defaults / coercion / extra-forbid.
  2. ``BotConfig.rerank_intent_whitelist`` round-trip through ``model_dump_json``
     so the Redis cache write/read path stays loss-free.
  3. ``_row_to_config`` mapper — JSONB dict → ``RerankIntentWhitelist``;
     malformed payload downgrades to None instead of raising.
  4. ``pipeline_config`` forward — both the worker and the test_chat builder
     forward ``bot_cfg.rerank_intent_whitelist`` so the rerank node can read
     it via ``_pcfg``.
  5. Rerank-node integration:
       - ``None`` (legacy) → reranker ``rerank()`` is awaited.
       - ``enabled=True`` + intent in list → reranker called.
       - ``enabled=True`` + intent NOT in list → reranker NOT called,
         mode metadata = ``intent_skip``.
       - ``enabled=False`` (override) → reranker called regardless of intent.
       - ``enabled=True`` + empty intents → ALL intents skip rerank
         (well-defined operator misconfig).
  6. Constants: starter intent set + enabled default match the analysis
     report (``factoid / comparison / aggregation / booking / yesno``).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.dto.bot_config import (
    BotConfig,
    RerankIntentWhitelist,
)
from ragbot.shared.constants import (
    DEFAULT_RERANK_INTENT_WHITELIST_ENABLED,
    DEFAULT_RERANK_WHITELIST_INTENTS,
)
from tests.conftest import TEST_TENANT_UUID


# ---------------------------------------------------------------------------
# 1. Pydantic model contract
# ---------------------------------------------------------------------------


def test_rerank_intent_whitelist_defaults_match_constants() -> None:
    """Default ``enabled`` flag tracks the constant so seed scripts and the
    Pydantic model agree without each carrying its own literal."""
    wl = RerankIntentWhitelist()
    assert wl.enabled is DEFAULT_RERANK_INTENT_WHITELIST_ENABLED
    assert wl.intents == ()


def test_rerank_intent_whitelist_starter_intents_constant_shape() -> None:
    """Starter set matches the TOP_SCORE_BOOST analysis recommendation —
    five intents where Jina rerank measurably lifts top_score."""
    assert "factoid" in DEFAULT_RERANK_WHITELIST_INTENTS
    assert "comparison" in DEFAULT_RERANK_WHITELIST_INTENTS
    assert "aggregation" in DEFAULT_RERANK_WHITELIST_INTENTS
    assert "booking" in DEFAULT_RERANK_WHITELIST_INTENTS
    assert "yesno" in DEFAULT_RERANK_WHITELIST_INTENTS
    # Anti-set: chitchat / off_topic must NOT be in the recommended starter
    # whitelist (they are the cost-saving bypass targets).
    assert "chitchat" not in DEFAULT_RERANK_WHITELIST_INTENTS
    assert "off_topic" not in DEFAULT_RERANK_WHITELIST_INTENTS


def test_intents_coerces_list_to_tuple() -> None:
    """JSONB returns a Python list — coerce to tuple so the field is hashable
    and immutable post-validation."""
    wl = RerankIntentWhitelist.model_validate(
        {"enabled": True, "intents": ["factoid", "yesno"]}
    )
    assert isinstance(wl.intents, tuple)
    assert wl.intents == ("factoid", "yesno")


def test_intents_dedupe_and_strip() -> None:
    """Operator copy/paste may leave whitespace / duplicates — strip and
    de-dupe preserving order."""
    wl = RerankIntentWhitelist.model_validate(
        {"intents": [" factoid ", "factoid", "yesno", "", "  "]}
    )
    assert wl.intents == ("factoid", "yesno")


def test_intents_drops_non_string_items() -> None:
    """A bad JSONB payload (e.g. a number snuck in) drops the bad item
    without breaking the whole whitelist."""
    wl = RerankIntentWhitelist.model_validate(
        {"intents": ["factoid", 123, None, "yesno"]}
    )
    assert wl.intents == ("factoid", "yesno")


def test_extra_field_rejected() -> None:
    """Typo on the JSONB column surfaces at load time, not silently at
    the gate."""
    with pytest.raises(Exception):  # pydantic ValidationError
        RerankIntentWhitelist.model_validate(
            {"enabled": True, "intents": ["factoid"], "typo_key": True}
        )


def test_botconfig_round_trip_through_json() -> None:
    """Redis cache writes ``BotConfig.model_dump_json()`` and reads back
    with ``model_validate_json``. Round-trip must preserve the whitelist."""
    cfg = BotConfig(
        id=uuid4(),
        bot_id="any-bot",
        channel_type="web",
        record_tenant_id=TEST_TENANT_UUID,
        workspace_id=str(TEST_TENANT_UUID),
        bot_name="any",
        rerank_intent_whitelist=RerankIntentWhitelist(
            enabled=True, intents=("factoid", "comparison"),
        ),
    )
    encoded = cfg.model_dump_json()
    decoded = BotConfig.model_validate_json(encoded)
    assert decoded.rerank_intent_whitelist is not None
    assert decoded.rerank_intent_whitelist.enabled is True
    assert decoded.rerank_intent_whitelist.intents == ("factoid", "comparison")


def test_botconfig_round_trip_null_whitelist() -> None:
    """Null whitelist (legacy bots) survives JSON round-trip as ``None``."""
    cfg = BotConfig(
        id=uuid4(),
        bot_id="any-bot",
        channel_type="web",
        record_tenant_id=TEST_TENANT_UUID,
        workspace_id=str(TEST_TENANT_UUID),
        bot_name="any",
    )
    decoded = BotConfig.model_validate_json(cfg.model_dump_json())
    assert decoded.rerank_intent_whitelist is None


# ---------------------------------------------------------------------------
# 2. _row_to_config mapper resilience
# ---------------------------------------------------------------------------


def _fake_row(**overrides):
    """Build a minimal duck-typed ORM row for ``_row_to_config``."""
    base = SimpleNamespace(
        id=uuid4(),
        bot_id="any-bot",
        channel_type="web",
        record_tenant_id=TEST_TENANT_UUID,
        workspace_id=str(TEST_TENANT_UUID),
        bot_name="any",
        record_model_id=None,
        record_embedding_model_id=None,
        system_prompt="",
        setting_options={},
        custom_vocabulary={},
        max_history=None,
        max_documents=10,
        prompt_max_tokens=None,
        rerank_top_n=5,
        plan_limits={},
        callback_url=None,
        language="vi",
        oos_answer_template=None,
        rerank_intent_whitelist=None,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_row_to_config_null_whitelist_returns_none() -> None:
    from ragbot.infrastructure.repositories.bot_repository import _row_to_config

    cfg = _row_to_config(_fake_row(rerank_intent_whitelist=None))
    assert cfg.rerank_intent_whitelist is None


def test_row_to_config_valid_dict_parses() -> None:
    from ragbot.infrastructure.repositories.bot_repository import _row_to_config

    cfg = _row_to_config(_fake_row(
        rerank_intent_whitelist={"enabled": True, "intents": ["factoid"]},
    ))
    assert cfg.rerank_intent_whitelist is not None
    assert cfg.rerank_intent_whitelist.intents == ("factoid",)


def test_row_to_config_malformed_payload_downgrades_to_none() -> None:
    """A typo on one bot's JSONB must not crash registry bootstrap for the
    whole platform — unparseable payload logs + downgrades to None."""
    from ragbot.infrastructure.repositories.bot_repository import _row_to_config

    cfg = _row_to_config(_fake_row(
        rerank_intent_whitelist={"enabled": "not-a-bool", "extra_typo": 1},
    ))
    assert cfg.rerank_intent_whitelist is None


# ---------------------------------------------------------------------------
# 3. pipeline_config forwarding from BOTH builders
# ---------------------------------------------------------------------------


def test_pipeline_config_forwards_whitelist_from_chat_worker() -> None:
    """Static-text assertion: ``chat_worker.build_pipeline_config`` lifts
    ``bot_cfg.rerank_intent_whitelist`` into pipeline_config so the rerank
    node can read it via ``_pcfg``."""
    from pathlib import Path

    # chat_worker was split into a package — scan every module.
    pkg = (
        Path(__file__).resolve().parents[2]
        / "src" / "ragbot" / "interfaces" / "workers" / "chat_worker"
    )
    body = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
    )
    assert '"rerank_intent_whitelist"' in body
    assert 'getattr(\n                bot_cfg, "rerank_intent_whitelist"' in body \
        or 'getattr(\n            bot_cfg, "rerank_intent_whitelist"' in body \
        or 'getattr(bot_cfg, "rerank_intent_whitelist"' in body


def test_pipeline_config_forwards_whitelist_from_test_chat_builder() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src" / "ragbot" / "interfaces" / "http" / "routes" / "test_chat"
        / "_pipeline_config.py"
    )
    body = src.read_text(encoding="utf-8")
    assert '"rerank_intent_whitelist"' in body


# ---------------------------------------------------------------------------
# 4. Rerank-node gate behaviour — direct invocation of the closure
# ---------------------------------------------------------------------------
#
# We exercise the real ``rerank`` async closure produced by ``build_graph``
# by reaching into the compiled StateGraph's nodes. This avoids reimplementing
# the gate logic in the test (a refactor target) while still being a proper
# unit test that does not need Redis / DB / live LLM.


class _CapturingReranker:
    """Reranker port stub that records every ``rerank()`` invocation."""

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
        # Echo the first ``top_n`` chunks with a fake high score so the
        # mode-aware threshold gate does not strip them.
        out: list[dict] = []
        for i, c in enumerate(chunks[:top_n]):
            row = dict(c)
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


def _extract_rerank_node():
    """Build a compiled graph and return the rerank node closure + tracker.

    LangGraph's ``compiled.nodes`` exposes the runnables; ``build_graph``
    keeps our async closures wrapped behind the runnable interface. We
    instead reach into a fresh build and pull the rerank closure out of
    the local namespace by capturing it via a custom ``step_tracker``."""
    # The cleanest entry-point is to call ``build_graph`` and trigger the
    # rerank step through a minimal state. Because the closure is module-
    # private we re-declare a thin orchestrator that mirrors the contract
    # without bringing the whole graph online.
    raise NotImplementedError("see _run_rerank_node helper below")


async def _run_rerank_node(
    *,
    intent: str,
    whitelist: RerankIntentWhitelist | None,
    reranker: _CapturingReranker,
) -> tuple[dict, _RecordingStepCtx]:
    """Drive the real rerank closure with a forged ``GraphState``.

    Strategy: call ``build_graph`` → hand-walk the compiled graph to find
    the ``rerank`` runnable → call it with our forged state. This keeps
    the test honest (real closure, real gate) without booting LangGraph's
    full async runtime.
    """
    from ragbot.orchestration.query_graph import build_graph

    # Minimal collaborators — only the bits the rerank node touches.
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

    # Pull the rerank runnable. Compiled LangGraph uses ``nodes`` dict.
    rerank_node = graph.nodes["rerank"]
    runnable = getattr(rerank_node, "runnable", None) or rerank_node
    bound = getattr(runnable, "bound", None)
    func = bound if bound is not None else runnable
    if hasattr(func, "afunc"):
        func = func.afunc
    elif hasattr(func, "func"):
        func = func.func

    state: dict = {
        "query": "any query",
        "rewritten_query": None,
        "retrieved_chunks": [
            {"chunk_id": "c1", "content": "a", "score": 0.5},
            {"chunk_id": "c2", "content": "b", "score": 0.4},
        ],
        "intent": intent,
        "pipeline_config": {
            "rerank_top_n": 2,
            "reranker_enabled": True,
            "reranker_min_score_active": 0.4,
            "reranker_min_score_bypass": 0.0,
            "rerank_intent_whitelist": whitelist,
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


def test_rerank_node_backcompat_null_whitelist_calls_reranker() -> None:
    """``None`` whitelist (legacy) preserves always-rerank behaviour."""
    rk = _CapturingReranker()
    out, ctx = asyncio.run(_run_rerank_node(
        intent="chitchat",
        whitelist=None,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"
    assert len(out["reranked_chunks"]) == 2


def test_rerank_node_intent_in_whitelist_calls_reranker() -> None:
    rk = _CapturingReranker()
    wl = RerankIntentWhitelist(enabled=True, intents=("factoid",))
    out, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        whitelist=wl,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"


def test_rerank_node_intent_not_in_whitelist_skips_reranker() -> None:
    """The cost-saving path: chitchat is NOT in the whitelist → rerank
    skipped entirely. Audit metadata captures the gated intent."""
    rk = _CapturingReranker()
    wl = RerankIntentWhitelist(enabled=True, intents=("factoid", "yesno"))
    out, ctx = asyncio.run(_run_rerank_node(
        intent="chitchat",
        whitelist=wl,
        reranker=rk,
    ))
    assert rk.calls == []
    assert ctx.metadata.get("mode") == "intent_skip"
    assert ctx.metadata.get("intent") == "chitchat"
    assert "factoid" in ctx.metadata.get("whitelist_intents", [])
    # Output preserves retrieval order, top_n applied (rerank_top_n=2).
    assert len(out["reranked_chunks"]) == 2


def test_rerank_node_enabled_false_overrides_to_always_rerank() -> None:
    """``enabled=False`` is the explicit A/B-disable — fires rerank even
    on intents that would otherwise be gated off."""
    rk = _CapturingReranker()
    wl = RerankIntentWhitelist(enabled=False, intents=("factoid",))
    out, ctx = asyncio.run(_run_rerank_node(
        intent="chitchat",
        whitelist=wl,
        reranker=rk,
    ))
    assert len(rk.calls) == 1
    assert ctx.metadata.get("mode") == "rerank"


def test_rerank_node_empty_intents_skips_all() -> None:
    """``enabled=True`` + empty intents = operator misconfig — still
    well-defined: skip rerank for ALL intents."""
    rk = _CapturingReranker()
    wl = RerankIntentWhitelist(enabled=True, intents=())
    out, ctx = asyncio.run(_run_rerank_node(
        intent="factoid",
        whitelist=wl,
        reranker=rk,
    ))
    assert rk.calls == []
    assert ctx.metadata.get("mode") == "intent_skip"
