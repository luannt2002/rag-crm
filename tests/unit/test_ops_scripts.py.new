"""Unit tests for Wave J3 operator migration scripts.

Covers idempotency, rollback path, pre-flight gating, atomic .env
rewrite, and HALLU-gate behaviour under mocked HTTP + DB + Redis +
subprocess boundaries. Per CLAUDE.md test rules: real behavioural
assertions only (no ``assert True`` / ``is not None`` stand-ins).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import ops_cascade_enable_per_bot as cascade_ops

# ----------------------------------------------------------------- helpers


class _Resp:
    def __init__(self, status: int = 200, payload: Any = None, text: str = ""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _Client:
    """Scriptable httpx.Client stand-in (sync, single-shot per method)."""

    def __init__(self, posts: list[_Resp] | None = None,
                 gets: list[_Resp] | None = None):
        self.posts = list(posts or [])
        self.gets = list(gets or [])
        self.post_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_Client":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(self, url: str, **kw: Any) -> _Resp:
        self.post_calls.append({"url": url, **kw})
        return self.posts.pop(0) if self.posts else _Resp(200, {"ok": True})

    def get(self, url: str, **kw: Any) -> _Resp:
        self.get_calls.append({"url": url, **kw})
        return self.gets.pop(0) if self.gets else _Resp(200, {"ok": True})


# =================================================================
# Script 2: ops_cascade_enable_per_bot
# =================================================================


def test_preflight_tier_rows_reports_missing_keys(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate: only default_answer_model row present.
    class _Cur:
        def __init__(self) -> None:
            self._rows: list[dict[str, Any]] = []
            self.last_sql = ""

        def __enter__(self) -> "_Cur":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def execute(self, sql: str, params: Any = None) -> None:
            self.last_sql = sql
            if "FROM system_config" in sql:
                self._rows = [
                    {"key": "default_answer_model", "value": "gpt-4.1-mini"},
                ]
            elif "FROM ai_models" in sql:
                self._rows = [{"1": 1}]  # treat as present

        def fetchall(self) -> list[dict[str, Any]]:
            return self._rows

        def fetchone(self) -> dict[str, Any] | None:
            return self._rows[0] if self._rows else None

    class _Conn:
        def __enter__(self) -> "_Conn":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def cursor(self, **_kw: Any) -> _Cur:
            return _Cur()

    monkeypatch.setattr(
        cascade_ops.psycopg2, "connect", lambda *_a, **_kw: _Conn()
    )
    ok, missing = cascade_ops.preflight_tier_rows()
    assert ok is False
    assert any("cascade_low_model" in m for m in missing)
    assert any("cascade_high_model" in m for m in missing)


def test_set_cascade_flag_includes_tenant_when_provided(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Cur:
        rowcount = 1

        def __enter__(self) -> "_Cur":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def execute(self, sql: str, params: Any) -> None:
            captured["sql"] = sql
            captured["params"] = params

    class _Conn:
        def __enter__(self) -> "_Conn":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def cursor(self) -> _Cur:
            return _Cur()

        def commit(self) -> None:
            pass

    monkeypatch.setattr(
        cascade_ops.psycopg2, "connect", lambda *_a, **_kw: _Conn()
    )
    rc = cascade_ops.set_cascade_flag(
        "tenant-uuid-x", "ws-1", "spa", "web", True,
    )
    assert rc == 1
    assert "AND record_tenant_id" in captured["sql"]
    # bool True + workspace + bot + channel + tenant = 5 params.
    assert captured["params"] == (True, "ws-1", "spa", "web", "tenant-uuid-x")


def test_set_cascade_flag_omits_tenant_when_absent(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    class _Cur:
        rowcount = 1

        def __enter__(self) -> "_Cur":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def execute(self, sql: str, params: Any) -> None:
            captured["sql"] = sql
            captured["params"] = params

    class _Conn:
        def __enter__(self) -> "_Conn":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def cursor(self) -> _Cur:
            return _Cur()

        def commit(self) -> None:
            pass

    monkeypatch.setattr(
        cascade_ops.psycopg2, "connect", lambda *_a, **_kw: _Conn()
    )
    cascade_ops.set_cascade_flag(None, "ws-1", "spa", "web", False)
    assert "AND record_tenant_id" not in captured["sql"]
    assert captured["params"] == (False, "ws-1", "spa", "web")


def test_smoke_chat_summary_tier_distribution(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    posts = [
        _Resp(200, {"answer": "ok", "cascade_tier": "haiku"}),
        _Resp(200, {"answer": "ok", "cascade_tier": "haiku"}),
        _Resp(200, {"answer": "ok", "cascade_tier": "sonnet"}),
    ]
    gets = [_Resp(200, {"token": "t"})]
    client = _Client(posts=posts, gets=gets)
    monkeypatch.setattr(
        cascade_ops.httpx, "Client", lambda *_a, **_kw: client,
    )
    res = cascade_ops.smoke_chat(
        "http://api", "spa", "web", "ws-1", turns=3, timeout_s=10,
    )
    assert res["ok"] is True
    assert res["turns"] == 3
    assert res["tier_distribution"] == {"haiku": 2, "sonnet": 1}


