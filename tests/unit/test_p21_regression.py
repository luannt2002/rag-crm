"""P21 regression tests — 4-layer audit rule failures found by deep-dive audit.

Covers:
1. `chat_worker.py` initial_state MUST write `record_tenant_id` / `record_bot_id`
   (the graph reads those; writing `tenant_id` / `bot_id` gives KeyError
    swallowed by the outer broad-except and kills the whole worker path).
2. `graph_retriever.graph_retrieve` MUST read `record_bot_id` (not `bot_id`)
   or GraphRAG never executes.
3. `_parse_intent_list` coerces admin-entered JSON string, list, CSV into a
   real list (was: substring-match on a string, fragile-by-accident).
4. Router cost uses `cached_input_per_1k_usd` when provided (e.g.
    gpt-4.1-mini = $0.10/M = 75% off), else falls back to a 50% discount.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ragbot.interfaces.workers.chat_worker import _parse_intent_list


_REPO_ROOT = Path(__file__).resolve().parents[2]


class TestP21StateKeyContract:
    """The 4-layer audit rule says: writer keys must match reader keys. These
    tests lock down the contract at the SOURCE TEXT level so a future rename
    of one side forces a rename on the other, instead of silently diverging."""

    def test_chat_worker_initial_state_uses_record_prefixed_keys(self):
        """Post ADR-W1-DI the state dict lives in graph_assembly — the
        record_* writer/reader contract is pinned on the canonical builder."""
        # chat_worker was split into a package — concatenate every sub-module.
        _cw_dir = _REPO_ROOT / "src/ragbot/interfaces/workers/chat_worker"
        worker_src = "\n".join(
            p.read_text() for p in sorted(_cw_dir.glob("*.py"))
        )
        assert "build_chat_initial_state(" in worker_src, (
            "chat_worker must build initial_state via the canonical builder"
        )
        assert "record_tenant_id=record_tenant_id," in worker_src
        assert "record_bot_id=record_bot_id," in worker_src
        asm_src = (
            _REPO_ROOT / "src/ragbot/orchestration/graph_assembly.py"
        ).read_text()
        assert '"record_tenant_id": record_tenant_id,' in asm_src, (
            "canonical builder must write record_tenant_id — the graph reads "
            "state['record_tenant_id']; a naked 'tenant_id' key silently breaks."
        )
        assert '"record_bot_id": record_bot_id,' in asm_src
        assert '"tenant_id": record_tenant_id,' not in asm_src
        assert '"bot_id": record_bot_id,' not in asm_src

    def test_graph_retriever_reads_record_bot_id(self):
        src = (_REPO_ROOT / "src/ragbot/infrastructure/graph/graph_retriever.py").read_text()
        assert 'state.get("record_bot_id")' in src, (
            "graph_retriever must read record_bot_id — GraphState has no 'bot_id' key "
            "so state.get('bot_id') silently returns None and GraphRAG never runs."
        )
        assert 'state.get("bot_id")' not in src, (
            "graph_retriever still reads 'bot_id' — rename to 'record_bot_id'."
        )

    def test_test_chat_build_graph_injects_session_factory(self):
        """Without session_factory, parent_child_expansion never fires even
        though admin enabled parent_child_enabled in system_config."""
        src = (_REPO_ROOT / "src/ragbot/interfaces/http/routes/test_chat/chat_routes.py").read_text()
        # Per-request fields are carried on GraphState via the canonical
        # builder (ADR-W1-DI). Both call sites — sync chat + stream — must
        # thread session_factory through build_chat_initial_state.
        assert src.count('session_factory=_opt("session_factory"),') >= 2, (
            "test_chat must inject session_factory into both initial states "
            "(chat + stream). Without it, parent-child expansion and GraphRAG "
            "silently skip even when admin enables them."
        )


class TestP21ParseIntentList:
    """Router does `intent in skip_rewrite_intents`. If the value is a JSON
    string, the `in` operator does substring match which works by accident
    for a single element. With two elements it breaks silently."""

    def test_parses_json_string(self):
        assert _parse_intent_list('["factoid"]') == ["factoid"]
        assert _parse_intent_list('["factoid", "chitchat"]') == ["factoid", "chitchat"]

    def test_passthrough_list(self):
        assert _parse_intent_list(["factoid", "chitchat"]) == ["factoid", "chitchat"]

    def test_csv_string_fallback(self):
        assert _parse_intent_list("factoid,chitchat") == ["factoid", "chitchat"]
        assert _parse_intent_list("factoid, chitchat") == ["factoid", "chitchat"]

    def test_empty_and_invalid(self):
        assert _parse_intent_list("") == []
        assert _parse_intent_list(None) == []
        # Malformed JSON → CSV fallback (treats whole value as one element)
        assert _parse_intent_list("[not-json") == ["[not-json"]

    def test_substring_bug_would_break_with_two_elements(self):
        """Historical bug: the raw string '["factoid", "multi_hop"]' made
        `"hop" in skip_rewrite` True accidentally. After parsing it's a list,
        so containment checks are exact."""
        parsed = _parse_intent_list('["factoid", "multi_hop"]')
        assert "hop" not in parsed, "substring-match regression — must be exact list containment"
        assert "factoid" in parsed
        assert "multi_hop" in parsed

    def test_canonical_prior_bug_exact_match_after_parse(self):
        """Lock the canonical pre-fix behaviour: `"factoid" in '["factoid"]'`
        returned True by accident (substring), hiding the issue for the common
        single-element case. After parse the check is against a list, so any
        non-exact token must not match even for the single-element case."""
        raw = '["factoid"]'
        # Prove the raw string had the substring-match trap:
        assert "factoid" in raw, "raw string substring test — documents the prior bug"
        # After parsing, containment is exact — no substring matches:
        parsed = _parse_intent_list(raw)
        assert parsed == ["factoid"]
        assert "fact" not in parsed
        assert "factoi" not in parsed
        assert "oid" not in parsed


class TestP21CachedPricing:
    """Router was hardcoding `/2` as cached-rate discount. For gpt-4.1-mini
    the real discount is 75% (cached $0.10/M, regular $0.40/M). When the DB
    provisions cached_input_per_1k_usd, the router must honor it."""

    def test_cached_rate_uses_explicit_field_when_provided(self):
        from ragbot.application.dto.model_runtime import Pricing

        p = Pricing(
            input_per_1k_usd=Decimal("0.0004"),
            output_per_1k_usd=Decimal("0.0016"),
            cached_input_per_1k_usd=Decimal("0.0001"),  # 75% off
        )
        # Simulate the router's cached-rate selection
        cached_rate = (
            p.cached_input_per_1k_usd
            if p.cached_input_per_1k_usd is not None
            else p.input_per_1k_usd / Decimal(2)
        )
        assert cached_rate == Decimal("0.0001")

    def test_cached_rate_falls_back_to_50_percent_when_not_provided(self):
        from ragbot.application.dto.model_runtime import Pricing

        p = Pricing(
            input_per_1k_usd=Decimal("0.003"),
            output_per_1k_usd=Decimal("0.015"),
            cached_input_per_1k_usd=None,
        )
        cached_rate = (
            p.cached_input_per_1k_usd
            if p.cached_input_per_1k_usd is not None
            else p.input_per_1k_usd / Decimal(2)
        )
        assert cached_rate == Decimal("0.0015")

    def test_router_source_uses_cached_input_field(self):
        """Lock the router to the correct accessor — regression against the
        previous hardcoded `/ Decimal(2)` line.

        After the token-instrumentation refactor (sync + stream + structured
        share ``compute_cost_usd``), the accessor lives inside that helper
        as ``getattr(pricing, "cached_input_per_1k_usd", None)``. The router
        module must still surface the literal field name OR call the shared
        helper so the cached-rate path runs.
        """
        src = (_REPO_ROOT / "src/ragbot/infrastructure/llm/dynamic_litellm_router.py").read_text()
        has_field_ref = "cached_input_per_1k_usd" in src
        has_compute_cost = "compute_cost_usd" in src
        assert has_field_ref and has_compute_cost, (
            "Router must read cached_input_per_1k_usd via compute_cost_usd — "
            "the field was loaded from DB but never used, making cost always "
            "50%-off regardless of provider's actual cache rate."
        )

    def test_router_compute_cost_helper_honors_cached_rate(self):
        """Call the helper directly: a model with a discounted cached rate
        produces a cost lower than the same prompt at full input rate."""
        from ragbot.infrastructure.llm.dynamic_litellm_router import (
            compute_cost_usd,
        )
        from ragbot.application.dto.model_runtime import Pricing

        pricing = Pricing(
            input_per_1k_usd=Decimal("0.003"),
            output_per_1k_usd=Decimal("0.015"),
            cached_input_per_1k_usd=Decimal("0.00075"),  # 75% off
        )
        # 1000 prompt tokens, 400 of which cached, no completion
        cost = compute_cost_usd(
            pricing,
            prompt_tokens=1000,
            completion_tokens=0,
            cached_tokens=400,
        )
        # Non-cached: 600/1000 * 0.003 = 0.0018
        # Cached:     400/1000 * 0.00075 = 0.0003
        # Total: 0.0021 — verifies the cached_input_per_1k_usd field is
        # actually consulted (would be 0.00225 at the 50% fallback rate).
        assert cost == Decimal("0.0021")


class TestP21MigrationSchema:
    """3 migrations (0028/0029/0030) hardcoded `SCHEMA = "ragbot"` while
    every other migration and models.py use `public`. On a fresh staging
    DB these would fail with `schema "ragbot" does not exist` and block the
    alembic chain from reaching HEAD. Lock all migrations to public schema."""

    def test_no_migration_hardcodes_ragbot_schema(self):
        """Scan only executable code (strip # comments + triple-quoted
        docstrings) so a mention in prose doesn't false-trigger."""
        import re

        migrations_dir = _REPO_ROOT / "alembic" / "versions"
        offenders: list[str] = []
        doc_re = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'')
        for f in sorted(migrations_dir.glob("*.py")):
            txt = f.read_text()
            # Strip triple-quoted docstrings + # line comments
            stripped = doc_re.sub("", txt)
            stripped = "\n".join(
                line.split("#", 1)[0] for line in stripped.splitlines()
            )
            if 'SCHEMA = "ragbot"' in stripped or "ragbot.document_chunks" in stripped or "ragbot.bots" in stripped:
                offenders.append(f.name)
        assert offenders == [], (
            f"Migrations must not hardcode 'ragbot' schema (models.py uses 'public'). "
            f"Offenders: {offenders}"
        )
