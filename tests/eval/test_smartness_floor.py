"""Smartness-floor regression test (Win-MVP P1-7).

Pins aggregate RAGAS faithfulness >= ``DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD``
(0.85) on a tiny domain-neutral golden set so any future PR that drops
generation/grounding quality below the production floor fails CI immediately.

Skipped by default (no ``EVAL_BOT_ID`` env). Default ``pytest tests/unit/``
does not collect this directory; CI opts in via:

    EVAL_BOT_ID=<bot> EVAL_TENANT_ID=<int> pytest -m eval

Design notes
------------
* Domain-neutral fixture (``golden_set.json``) — no brand / tenant literal.
* Aggregate floor (not per-item) — 1 flaky judge call must not break CI.
* ``REFUSE`` / ``GREET`` items contribute trivially (no factual claims =>
  faithfulness defaults to 1.0 in :class:`LLMRagasEvaluator`), so the
  effective floor is driven by ``PASS`` items.
* ``debug=full`` is requested so the response carries
  ``retrieved_chunks_content`` — RAGAS scores chunk *content*, not previews.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from ragbot.evaluation.ragas_metrics import LLMRagasEvaluator, TurnInput
from ragbot.shared.constants import (
    DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD,
    DEFAULT_RAGAS_MAX_CONCURRENCY,
)

# --- module constants -------------------------------------------------------
GOLDEN_PATH = Path(__file__).parent / "golden_set.json"
DEFAULT_CHANNEL_TYPE = "web"
DEFAULT_CONNECT_ID_PREFIX = "smartness-floor-"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"
DEBUG_FULL = "full"
HTTP_OK = 200
REQUEST_TIMEOUT_S = 60.0


pytestmark = [pytest.mark.eval]


def _load_golden() -> dict:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _resolve_eval_env() -> tuple[str, str, int, str]:
    """Read EVAL_* env. Skip the test cleanly if any required key is missing.

    Returns ``(base_url, bot_id, tenant_id, channel_type)``.
    """
    bot_id = os.getenv("EVAL_BOT_ID", "").strip()
    tenant_raw = os.getenv("EVAL_TENANT_ID", "").strip()
    if not bot_id or not tenant_raw:
        pytest.skip(
            "smartness-floor: EVAL_BOT_ID + EVAL_TENANT_ID env required "
            "(opt-in CI gate; dev runs skip cleanly)"
        )
    try:
        tenant_id = int(tenant_raw)
    except ValueError:
        pytest.skip(f"smartness-floor: EVAL_TENANT_ID must be int, got {tenant_raw!r}")
    base_url = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
    channel_type = os.getenv("EVAL_CHANNEL_TYPE", DEFAULT_CHANNEL_TYPE)
    return base_url, bot_id, tenant_id, channel_type


async def _ask_one(client, *, base_url: str, token: str, payload: dict) -> dict:
    r = await client.post(
        f"{base_url}{CHAT_PATH}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT_S,
    )
    if r.status_code != HTTP_OK:
        return {"_error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return r.json()


def _extract_chunk_texts(body: dict) -> tuple[str, ...]:
    """Prefer full ``retrieved_chunks_content`` (debug=full), fall back to
    source previews so the test still scores when debug is gated off."""
    chunks: list[str] = []
    for c in body.get("retrieved_chunks_content") or []:
        if isinstance(c, dict):
            text = c.get("content") or c.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text)
    if chunks:
        return tuple(chunks)
    for s in body.get("sources") or []:
        if isinstance(s, dict):
            preview = s.get("preview") or s.get("content")
            if isinstance(preview, str) and preview.strip():
                chunks.append(preview)
    return tuple(chunks)


@pytest.mark.asyncio
async def test_smartness_floor_faithfulness_ge_threshold():
    """Aggregate faithfulness over the golden set must clear the 0.85 floor.

    Failure modes this test catches:
      * Generation regression (LLM degenerates / hallucinates more often).
      * Retrieval regression (chunks no longer support the answer).
      * Prompt-engineering regression (system prompt change drops grounding).
      * Cache-poisoning / config-drift bugs that silently mute citations.
    """
    httpx = pytest.importorskip("httpx", reason="httpx not installed")

    base_url, bot_id, tenant_id, channel_type = _resolve_eval_env()
    golden = _load_golden()
    items = golden.get("items") or []
    assert items, "golden_set.json: no items"

    async with httpx.AsyncClient() as client:
        # Self-token (test harness only — not production auth path).
        try:
            tok_resp = await client.get(f"{base_url}{SELF_TOKEN_PATH}", timeout=REQUEST_TIMEOUT_S)
        except (httpx.HTTPError, OSError) as exc:
            pytest.skip(f"smartness-floor: ragbot-api not reachable at {base_url}: {exc!s}")
        if tok_resp.status_code != HTTP_OK:
            pytest.skip(f"smartness-floor: self-token endpoint returned {tok_resp.status_code}")
        token = tok_resp.json().get("token")
        if not token:
            pytest.skip("smartness-floor: self-token endpoint did not return a token")

        # Fan-out chat calls (sequential — keeps test deterministic & cheap).
        bodies: list[dict] = []
        for it in items:
            payload = {
                "tenant_id": tenant_id,
                "bot_id": bot_id,
                "channel_type": channel_type,
                "connect_id": f"{DEFAULT_CONNECT_ID_PREFIX}{it['id']}",
                "question": it["question"],
                "bypass_cache": True,
                "debug": DEBUG_FULL,
            }
            body = await _ask_one(client, base_url=base_url, token=token, payload=payload)
            bodies.append(body)

    # Score with the production RAGAS evaluator.
    evaluator = LLMRagasEvaluator(max_concurrency=DEFAULT_RAGAS_MAX_CONCURRENCY)
    scoring = []
    for it, body in zip(items, bodies, strict=True):
        if "_error" in body:
            pytest.skip(f"smartness-floor: chat error on {it['id']}: {body['_error']}")
        turn = TurnInput(
            question=it["question"],
            answer=body.get("answer") or "",
            retrieved_chunks=_extract_chunk_texts(body),
        )
        scoring.append(evaluator.score_turn(turn))

    scores = await asyncio.gather(*scoring)
    faiths = [s.faithfulness for s in scores]
    assert faiths, "no faithfulness scores produced"

    avg = sum(faiths) / len(faiths)
    floor = DEFAULT_DEEPEVAL_FAITHFULNESS_THRESHOLD
    breakdown = ", ".join(
        f"{it['id']}={s.faithfulness:.2f}" for it, s in zip(items, scores, strict=True)
    )
    assert avg >= floor, (
        f"smartness floor breach: aggregate faithfulness {avg:.3f} < {floor:.2f}\n"
        f"per-item: {breakdown}"
    )


def test_golden_set_is_domain_neutral():
    """Fixture sanity — fail fast if a future contributor leaks a brand /
    tenant literal into the golden set, which would couple CI to one
    customer corpus."""
    raw = _load_golden()
    assert raw.get("domain_neutral") is True, "golden_set.json must declare domain_neutral=true"
    items = raw.get("items") or []
    assert 10 <= len(items) <= 20, f"expected 10-20 items, got {len(items)}"
    for it in items:
        for forbidden in ("tenant_id", "bot_id", "channel_type", "connect_id"):
            assert forbidden not in it, (
                f"{it['id']} leaks runtime field {forbidden!r}; must be injected at run time"
            )
        assert it.get("question"), f"{it['id']} missing question"
        assert it.get("category"), f"{it['id']} missing category"
        assert it.get("expected_classification") in {"PASS", "REFUSE", "GREET"}, (
            f"{it['id']} bad expected_classification {it.get('expected_classification')!r}"
        )
