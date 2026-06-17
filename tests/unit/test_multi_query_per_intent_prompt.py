"""Unit tests — per-intent rewrite-prompt dispatch in multi_query_expansion.

T1.5.S25 (Multi-HyDE, Gao et al. 2025 paper 16). The legacy default emits
*equivalent paraphrases* — same semantics, different wording — which leaves
recall flat because every variant retrieves overlapping chunks. Multi-HyDE
prescribes *non-equivalent* variants per intent so each branch hits a
different chunk set; RRF then merges across these complementary lists.

Prompt verbatim text now lives in the ``language_packs`` table (seeded by
alembic 0099) — these tests stub a fake ``LanguagePackPort`` that records
which ``(language, prompt_key)`` the service asked for and returns a
sentinel template so the dispatch path can be verified without depending
on the seed migration.

Coverage:
  - factoid    → ``multi_query_factoid_prompt`` key.
  - multi_hop  → ``multi_query_multi_hop_prompt`` key.
  - comparison → ``multi_query_comparison_prompt`` key.
  - aggregation → ``multi_query_aggregation_prompt`` key.
  - synthesis  → aliases to ``multi_query_aggregation_prompt`` (HyDE hypothesis).
  - chitchat / out_of_scope intent → expand_query still runs (LLM gate
      lives in query_graph.py); template falls back to default since
      these keys are NOT in ``MULTI_QUERY_INTENT_PROMPT_KEYS``.
  - unknown intent string → default paraphrase key (no KeyError).
  - explicit ``system_prompt`` override → wins over intent dispatch.
  - intent=None → default paraphrase key (backwards compat).
  - language_pack_service=None → empty system prompt (graceful degrade).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ragbot.application.services.multi_query_expansion import expand_query
from ragbot.shared.constants import (
    DEFAULT_MULTI_QUERY_PROMPT_KEY,
    MULTI_QUERY_INTENT_PROMPT_KEYS,
)


def _run(coro):
    return asyncio.run(coro)


class _StubLanguagePack:
    """Minimal LanguagePackPort stub for dispatch verification.

    Returns a deterministic sentinel template per (language, prompt_key)
    so tests can assert which key the service requested without coupling
    to the DB seed text. ``calls`` records each request for inspection.
    """

    def __init__(self, *, raise_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self._raise_on = raise_on or set()

    async def get(self, language: str, prompt_key: str) -> str:
        self.calls.append((language, prompt_key))
        if prompt_key in self._raise_on:
            raise KeyError(prompt_key)
        return f"SENTINEL[{language}:{prompt_key}]({{n}})"

    async def get_pack(self, language: str) -> dict[str, str]:
        raise NotImplementedError  # pragma: no cover


def _make_capture_llm(reply_variants: list[str]) -> tuple[AsyncMock, dict[str, Any]]:
    """Build a mock LLM that captures ``messages`` and returns a JSON array.

    Returns ``(mock, captured)`` — ``captured["system"]`` is filled with the
    system prompt the service sent on the most recent call.
    """
    captured: dict[str, Any] = {"system": None, "user": None}

    async def _fake(*, model_id: str, messages: list[dict], timeout_s: int) -> dict:
        for msg in messages:
            if msg.get("role") == "system":
                captured["system"] = msg.get("content")
            elif msg.get("role") == "user":
                captured["user"] = msg.get("content")
        return {"text": json.dumps(reply_variants)}

    return AsyncMock(side_effect=_fake), captured


def _expected_sentinel(prompt_key: str, *, n: int, language: str = "vi") -> str:
    """Format the stub sentinel template the same way the service does."""
    return f"SENTINEL[{language}:{prompt_key}]({n})"


# --------------------------------------------------------------------------- #
# Per-intent dispatch                                                         #
# --------------------------------------------------------------------------- #


def test_factoid_intent_uses_paraphrase_key() -> None:
    """factoid → ``multi_query_factoid_prompt`` key."""
    llm, cap = _make_capture_llm(["q1", "q2"])
    lps = _StubLanguagePack()
    out = _run(expand_query(
        "câu hỏi gốc",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="factoid",
        language_pack_service=lps,
    ))
    assert len(out) == 3
    assert ("vi", MULTI_QUERY_INTENT_PROMPT_KEYS["factoid"]) in lps.calls
    assert cap["system"] == _expected_sentinel(
        MULTI_QUERY_INTENT_PROMPT_KEYS["factoid"], n=2
    )


def test_multi_hop_intent_uses_decompose_key() -> None:
    """multi_hop → ``multi_query_multi_hop_prompt`` key."""
    llm, cap = _make_capture_llm(["sub1", "sub2"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "câu hỏi đa-bước",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="multi_hop",
        language_pack_service=lps,
    ))
    assert ("vi", MULTI_QUERY_INTENT_PROMPT_KEYS["multi_hop"]) in lps.calls
    assert cap["system"] == _expected_sentinel(
        MULTI_QUERY_INTENT_PROMPT_KEYS["multi_hop"], n=2
    )
    # multi_hop key MUST differ from factoid (else it's just a relabel).
    assert MULTI_QUERY_INTENT_PROMPT_KEYS["multi_hop"] != MULTI_QUERY_INTENT_PROMPT_KEYS["factoid"]


def test_comparison_intent_uses_entity_pair_key() -> None:
    """comparison → ``multi_query_comparison_prompt`` key."""
    llm, cap = _make_capture_llm(["entA", "entB"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "so sánh A và B",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="comparison",
        language_pack_service=lps,
    ))
    assert ("vi", MULTI_QUERY_INTENT_PROMPT_KEYS["comparison"]) in lps.calls
    assert cap["system"] == _expected_sentinel(
        MULTI_QUERY_INTENT_PROMPT_KEYS["comparison"], n=2
    )
    assert MULTI_QUERY_INTENT_PROMPT_KEYS["comparison"] != MULTI_QUERY_INTENT_PROMPT_KEYS["factoid"]


def test_aggregation_intent_uses_multi_attribute_key() -> None:
    """aggregation → ``multi_query_aggregation_prompt`` key."""
    llm, cap = _make_capture_llm(["attr1", "attr2"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "tổng hợp X Y Z",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="aggregation",
        language_pack_service=lps,
    ))
    assert ("vi", MULTI_QUERY_INTENT_PROMPT_KEYS["aggregation"]) in lps.calls
    assert cap["system"] == _expected_sentinel(
        MULTI_QUERY_INTENT_PROMPT_KEYS["aggregation"], n=2
    )
    assert MULTI_QUERY_INTENT_PROMPT_KEYS["aggregation"] != MULTI_QUERY_INTENT_PROMPT_KEYS["factoid"]


def test_synthesis_intent_aliases_aggregation_key() -> None:
    """synthesis reuses the aggregation template (HyDE hypothesis flavour)."""
    llm, cap = _make_capture_llm(["h1", "h2"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "câu hỏi tổng hợp",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="synthesis",
        language_pack_service=lps,
    ))
    # Synthesis aliases aggregation in the registry.
    assert MULTI_QUERY_INTENT_PROMPT_KEYS["synthesis"] == MULTI_QUERY_INTENT_PROMPT_KEYS["aggregation"]
    assert ("vi", MULTI_QUERY_INTENT_PROMPT_KEYS["synthesis"]) in lps.calls
    assert cap["system"] == _expected_sentinel(
        MULTI_QUERY_INTENT_PROMPT_KEYS["synthesis"], n=2
    )


# --------------------------------------------------------------------------- #
# Fallback / override semantics                                               #
# --------------------------------------------------------------------------- #


def test_unknown_intent_falls_back_to_default_key() -> None:
    """Unknown intent string → default paraphrase key; no KeyError."""
    llm, cap = _make_capture_llm(["q1"])
    lps = _StubLanguagePack()
    out = _run(expand_query(
        "câu hỏi",
        n_variants=2,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="weather_forecast_intent_does_not_exist",
        language_pack_service=lps,
    ))
    assert len(out) >= 1
    assert ("vi", DEFAULT_MULTI_QUERY_PROMPT_KEY) in lps.calls
    assert cap["system"] == _expected_sentinel(DEFAULT_MULTI_QUERY_PROMPT_KEY, n=1)


def test_intent_none_uses_default_key() -> None:
    """intent=None (default) preserves legacy paraphrase contract."""
    llm, cap = _make_capture_llm(["q1", "q2"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "câu hỏi",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent=None,
        language_pack_service=lps,
    ))
    assert ("vi", DEFAULT_MULTI_QUERY_PROMPT_KEY) in lps.calls
    assert cap["system"] == _expected_sentinel(DEFAULT_MULTI_QUERY_PROMPT_KEY, n=2)


def test_explicit_system_prompt_override_wins() -> None:
    """When caller passes explicit system_prompt, intent dispatch is bypassed."""
    custom = "CUSTOM PROMPT — produce {n} variants."
    llm, cap = _make_capture_llm(["v1", "v2"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "câu hỏi",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="multi_hop",
        system_prompt=custom,
        language_pack_service=lps,
    ))
    assert cap["system"] == custom.format(n=2), "explicit override must beat intent dispatch"
    # When override is supplied, the LPS must NOT be queried.
    assert lps.calls == [], "explicit system_prompt must skip the LPS lookup"


def test_chitchat_intent_falls_back_to_default_key() -> None:
    """chitchat is NOT in MULTI_QUERY_INTENT_PROMPT_KEYS — caller skip-gate
    happens upstream in query_graph; if we ever reach here, default applies.
    """
    llm, cap = _make_capture_llm(["q1"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "xin chào",
        n_variants=2,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="chitchat",
        language_pack_service=lps,
    ))
    assert "chitchat" not in MULTI_QUERY_INTENT_PROMPT_KEYS, (
        "chitchat must NOT have its own key — gate skip lives in query_graph"
    )
    assert ("vi", DEFAULT_MULTI_QUERY_PROMPT_KEY) in lps.calls
    assert cap["system"] == _expected_sentinel(DEFAULT_MULTI_QUERY_PROMPT_KEY, n=1)


def test_out_of_scope_intent_falls_back_to_default_key() -> None:
    """out_of_scope is NOT in MULTI_QUERY_INTENT_PROMPT_KEYS — defaults apply."""
    llm, cap = _make_capture_llm(["q1"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "câu hỏi ngoài phạm vi",
        n_variants=2,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="out_of_scope",
        language_pack_service=lps,
    ))
    assert "out_of_scope" not in MULTI_QUERY_INTENT_PROMPT_KEYS
    assert ("vi", DEFAULT_MULTI_QUERY_PROMPT_KEY) in lps.calls
    assert cap["system"] == _expected_sentinel(DEFAULT_MULTI_QUERY_PROMPT_KEY, n=1)


def test_language_pack_service_none_emits_empty_system_prompt() -> None:
    """Without DI service the function emits empty prompt — graceful degrade.

    Legacy callers that don't pass ``language_pack_service`` still
    function — the LLM receives an empty system prompt and produces
    paraphrases best-effort; the contract fallback covers any failure.
    """
    llm, cap = _make_capture_llm(["q1", "q2"])
    out = _run(expand_query(
        "câu hỏi",
        n_variants=3,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="multi_hop",
        # language_pack_service intentionally omitted
    ))
    assert cap["system"] == ""
    assert len(out) >= 1  # variant-0 still emitted via _finalise safety net.


def test_language_pack_lookup_failure_falls_back_gracefully() -> None:
    """LPS.get raising → empty prompt, no exception leak to caller."""
    llm, cap = _make_capture_llm(["q1"])
    lps = _StubLanguagePack(
        raise_on={MULTI_QUERY_INTENT_PROMPT_KEYS["multi_hop"]}
    )
    out = _run(expand_query(
        "câu hỏi",
        n_variants=2,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="multi_hop",
        language_pack_service=lps,
    ))
    assert cap["system"] == ""
    # original query still emitted as variant-0.
    assert "câu hỏi" in out


def test_language_param_forwarded_to_lps() -> None:
    """Non-default language code must reach the LPS request."""
    llm, _ = _make_capture_llm(["q1"])
    lps = _StubLanguagePack()
    _run(expand_query(
        "question",
        n_variants=2,
        model_id="x",
        timeout_s=5,
        llm_complete_fn=llm,
        intent="factoid",
        language="en",
        language_pack_service=lps,
    ))
    assert ("en", MULTI_QUERY_INTENT_PROMPT_KEYS["factoid"]) in lps.calls


# --------------------------------------------------------------------------- #
# Registry invariants                                                         #
# --------------------------------------------------------------------------- #


def test_intent_prompt_key_registry_has_all_five_required_keys() -> None:
    """Mission spec keys: factoid, multi_hop, comparison, aggregation, synthesis."""
    required = {"factoid", "multi_hop", "comparison", "aggregation", "synthesis"}
    assert required.issubset(set(MULTI_QUERY_INTENT_PROMPT_KEYS.keys()))


def test_intent_prompt_keys_all_end_with_prompt_suffix() -> None:
    """All registry values must be language_packs prompt-key naming convention."""
    for intent, key in MULTI_QUERY_INTENT_PROMPT_KEYS.items():
        assert key.startswith("multi_query_"), f"intent={intent!r} key={key!r}"
        assert key.endswith("_prompt"), f"intent={intent!r} key={key!r}"
