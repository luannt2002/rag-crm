"""Unit coverage for `scripts/test_75q_load.py::ask_with_token_refresh`.

R4 OLD-half load run failed 41/75 turns with HTTP 401 starting at room 3
idx 4 — the self-issued JWT expired mid-flight and the harness sent it
unchanged on every subsequent turn. This module pins the new behavior:

1. 401 → re-fetch token, retry once, succeed.
2. 401 → refresh → 401 again → return the second 401 so the caller
   records the failure (no silent infinite loop).
3. 200 first try → no token refresh attempted.
4. Refreshed token mutates `token_box` so subsequent turns reuse it.

The harness is a script (not a package), so it is loaded by file path
via `importlib.util` — same shape used by other `tests/unit/` files
covering `scripts/`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Module loader — `scripts/` is not a package, so import-by-path.
# ---------------------------------------------------------------------------


def _load_harness() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "test_75q_load.py"
    spec = importlib.util.spec_from_file_location("_t75q_harness", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def harness() -> ModuleType:
    return _load_harness()


# ---------------------------------------------------------------------------
# Stub helpers — replace `ask_once` and `get_self_token` with deterministic
# scripted responses so the test is fully offline.
# ---------------------------------------------------------------------------


class _AskOnceStub:
    """Returns successive (status, body, wall_ms) triples per call."""

    def __init__(self, results: list[tuple[int, dict[str, Any], float]]) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        client: Any,
        *,
        base_url: str,
        token: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any], float]:
        self.calls.append({"token": token, "payload": payload, "base_url": base_url})
        if not self._results:
            raise AssertionError("ask_once stub exhausted — too many calls")
        return self._results.pop(0)


class _GetTokenStub:
    """Returns successive token strings per call; tracks invocations."""

    def __init__(self, tokens: list[str]) -> None:
        self._tokens = list(tokens)
        self.calls = 0

    async def __call__(self, client: Any, base_url: str) -> str:
        self.calls += 1
        if not self._tokens:
            raise AssertionError("get_self_token stub exhausted")
        return self._tokens.pop(0)


def _patch(harness: ModuleType, monkeypatch: pytest.MonkeyPatch, *,
           ask_results: list[tuple[int, dict[str, Any], float]],
           refresh_tokens: list[str]) -> tuple[_AskOnceStub, _GetTokenStub]:
    ask_stub = _AskOnceStub(ask_results)
    tok_stub = _GetTokenStub(refresh_tokens)
    monkeypatch.setattr(harness, "ask_once", ask_stub)
    monkeypatch.setattr(harness, "get_self_token", tok_stub)
    return ask_stub, tok_stub


# ---------------------------------------------------------------------------
# Test 1 — 401 → token refresh + retry succeeds.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_then_refresh_then_200_succeeds(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_stub, tok_stub = _patch(
        harness,
        monkeypatch,
        ask_results=[
            (harness.HTTP_UNAUTHORIZED, {"_body": "invalid token"}, 12.0),
            (harness.HTTP_OK, {"answer": "ok"}, 34.0),
        ],
        refresh_tokens=["NEW_TOKEN"],
    )
    box: dict[str, str] = {"token": "STALE"}
    status, body, wall_ms = await harness.ask_with_token_refresh(
        client=None, base_url="http://x", token_box=box, payload={"q": "hi"}
    )
    assert status == harness.HTTP_OK
    assert body == {"answer": "ok"}
    assert wall_ms == 34.0
    # Token rotated and second call used the new token.
    assert tok_stub.calls == 1
    assert box["token"] == "NEW_TOKEN"
    assert len(ask_stub.calls) == 2
    assert ask_stub.calls[0]["token"] == "STALE"
    assert ask_stub.calls[1]["token"] == "NEW_TOKEN"


# ---------------------------------------------------------------------------
# Test 2 — 401 then 401 → bounded by MAX_TOKEN_REFRESH_RETRIES, surfaces 401.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_401_twice_returns_last_unauthorized(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_stub, tok_stub = _patch(
        harness,
        monkeypatch,
        ask_results=[
            (harness.HTTP_UNAUTHORIZED, {"_body": "invalid token"}, 11.0),
            (harness.HTTP_UNAUTHORIZED, {"_body": "still invalid"}, 13.0),
        ],
        refresh_tokens=["REFRESHED_TOKEN"],
    )
    box: dict[str, str] = {"token": "STALE"}
    status, body, wall_ms = await harness.ask_with_token_refresh(
        client=None, base_url="http://x", token_box=box, payload={"q": "hi"}
    )
    # Caller sees the second 401 — they classify it as ERROR + record it.
    assert status == harness.HTTP_UNAUTHORIZED
    assert body == {"_body": "still invalid"}
    assert wall_ms == 13.0
    # Exactly MAX_TOKEN_REFRESH_RETRIES POSTs and 1 token refresh between them.
    assert len(ask_stub.calls) == harness.MAX_TOKEN_REFRESH_RETRIES
    assert tok_stub.calls == 1
    # Final loop iteration must NOT trigger another refresh after the 401.
    assert len(ask_stub.calls) == 2


# ---------------------------------------------------------------------------
# Test 3 — first call 200 → no refresh attempted.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_ok_skips_refresh(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_stub, tok_stub = _patch(
        harness,
        monkeypatch,
        ask_results=[(harness.HTTP_OK, {"answer": "ok"}, 7.0)],
        refresh_tokens=[],  # never called
    )
    box: dict[str, str] = {"token": "FRESH"}
    status, body, wall_ms = await harness.ask_with_token_refresh(
        client=None, base_url="http://x", token_box=box, payload={"q": "hi"}
    )
    assert status == harness.HTTP_OK
    assert body == {"answer": "ok"}
    assert wall_ms == 7.0
    assert tok_stub.calls == 0
    assert box["token"] == "FRESH"
    assert len(ask_stub.calls) == 1


# ---------------------------------------------------------------------------
# Test 4 — token-box mutation is visible to the caller (so the next turn
# does NOT replay the stale JWT). This is the load-run regression we hit.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_box_mutation_visible_across_calls(
    harness: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    ask_stub, tok_stub = _patch(
        harness,
        monkeypatch,
        ask_results=[
            # Call 1 — 401, refresh, retry succeeds.
            (harness.HTTP_UNAUTHORIZED, {"_body": "expired"}, 1.0),
            (harness.HTTP_OK, {"answer": "first"}, 2.0),
            # Call 2 — 200 immediately on the rotated token (no refresh).
            (harness.HTTP_OK, {"answer": "second"}, 3.0),
        ],
        refresh_tokens=["NEW_JWT"],
    )
    box: dict[str, str] = {"token": "OLD_JWT"}

    # First wrapper call — should rotate the box.
    s1, b1, _ = await harness.ask_with_token_refresh(
        client=None, base_url="http://x", token_box=box, payload={"q": "q1"}
    )
    assert s1 == harness.HTTP_OK and b1 == {"answer": "first"}
    assert box["token"] == "NEW_JWT", "token_box must be mutated for caller"

    # Second wrapper call — must reuse the rotated token, no extra refresh.
    s2, b2, _ = await harness.ask_with_token_refresh(
        client=None, base_url="http://x", token_box=box, payload={"q": "q2"}
    )
    assert s2 == harness.HTTP_OK and b2 == {"answer": "second"}
    assert tok_stub.calls == 1, "no second refresh — first one is still valid"
    # The third underlying ask_once call carried NEW_JWT, never OLD_JWT again.
    assert ask_stub.calls[2]["token"] == "NEW_JWT"
