"""Integration tests for ``/api/ragbot/test/chat-async`` (mega-sprint-G26).

Architecture (sibling Coder-D1 owns the worker, this module owns the HTTP
seam):

::

    POST /test/chat-async  → XADD chat.requested  → returns {job_id}
                                                       │
                                              chat_async_worker.py
                                                       │
                                       HSET chat:result:{job_id}
                                                       ▼
    GET  /test/chat-async/{job_id} ← HGETALL ← returns {status, answer, …}

Why integration scope (not pure unit):
    The route module is a thin orchestrator over ``app.state.container``,
    Redis Streams + Hashes, and a tenant-scoped 4-key bot lookup. The
    behavioural contract worth pinning is the **wire shape** the worker
    consumes (Stream entry layout, hash key prefix, JSON payload schema)
    + the **poll shape** the client receives — both of which depend on
    the live FastAPI app + router wiring. We boot the route through
    ``TestClient`` against an in-process FakeRedis double so the test
    exercises the real handler without a Redis server.

The mock container exposes only the seams the route reads:
    * ``container.bot_repo().find_by_4key(...)``  → returns a stub bot
    * ``container.system_config_service().get_int(...)`` → defaults
    * ``container.redis_client()`` → FakeRedis (in-process)

Coverage:
    1. POST returns ``job_id`` + ``status="pending"`` and writes to the
       Stream with the correct layout.
    2. POST refuses with ``QUOTA_EXHAUSTED`` envelope when the bot is
       out of tokens (DB-driven refusal text, no app-injected literal).
    3. POST 404s when the bot is not found.
    4. GET returns ``status=pending`` when the result hash is absent.
    5. GET returns the full envelope after the worker writes the hash.
    6. GET returns ``status=error`` when the worker recorded an error.

mega-sprint-G26 — Wave D LLM-async-queue.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragbot.shared.constants import (
    CHAT_REQUEST_STREAM,
    CHAT_RESULT_HASH_PREFIX,
)


# ---------------------------------------------------------------------------
# In-process Redis double — implements only the verbs the route + worker use.
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Async fake exposing ``xadd`` / ``hgetall`` / ``hset`` / ``expire`` / ``get``.

    Self-contained so the test does not require the optional ``fakeredis``
    package — keeps the unit-suite green on minimal CI envs while still
    exercising the real route handlers end-to-end.
    """

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[bytes, bytes]]]] = {}
        self.hashes: dict[str, dict[bytes, bytes]] = {}
        self.scalars: dict[str, bytes] = {}
        self.expires: dict[str, int] = {}
        self._auto_id = 0

    @staticmethod
    def _to_bytes(value: Any) -> bytes:
        if isinstance(value, bytes):
            return value
        return str(value).encode("utf-8")

    async def xadd(self, stream: str, fields: dict[str, Any]) -> bytes:
        self._auto_id += 1
        msg_id = f"{self._auto_id}-0"
        encoded = {self._to_bytes(k): self._to_bytes(v) for k, v in fields.items()}
        self.streams.setdefault(stream, []).append((msg_id, encoded))
        return msg_id.encode("utf-8")

    async def hgetall(self, key: str) -> dict[bytes, bytes]:
        return dict(self.hashes.get(key, {}))

    async def hset(
        self,
        key: str,
        field: Any | None = None,
        value: Any | None = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        bucket = self.hashes.setdefault(key, {})
        added = 0
        if mapping:
            for k, v in mapping.items():
                bk = self._to_bytes(k)
                if bk not in bucket:
                    added += 1
                bucket[bk] = self._to_bytes(v)
        if field is not None and value is not None:
            bk = self._to_bytes(field)
            if bk not in bucket:
                added += 1
            bucket[bk] = self._to_bytes(value)
        return added

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = int(ttl)
        return True

    async def get(self, key: str) -> bytes | None:
        return self.scalars.get(key)

    async def set(self, key: str, value: Any) -> bool:
        self.scalars[key] = self._to_bytes(value)
        return True


# ---------------------------------------------------------------------------
# App fixture — wires a noop lifespan + mock container around the real router.
# ---------------------------------------------------------------------------


def _stub_bot(
    *,
    bot_uuid: uuid.UUID,
    extra_max_tokens: int = 0,
    tokens_used: int = 0,
    bypass_token_check: bool = False,
    oos_template: str = "",
) -> MagicMock:
    """Build a bot-config stub matching the attrs the route reads.

    Keep the stub minimal — only the attributes the route actually
    touches. A spec'd MagicMock would over-couple the test to the
    repository row schema (which is owned by ``BotConfig`` upstream).
    """
    bot_cfg = MagicMock()
    bot_cfg.id = bot_uuid
    bot_cfg.extra_max_tokens = extra_max_tokens
    bot_cfg.tokens_used = tokens_used
    bot_cfg.bypass_token_check = bypass_token_check
    bot_cfg.oos_answer_template = oos_template
    return bot_cfg


def _build_test_app(
    *,
    fake_redis: _FakeRedis,
    bot_cfg: Any | None,
    system_max_tokens: int = 1_000_000,
) -> FastAPI:
    """Assemble a FastAPI app with the chat_async router + mock container.

    We import the router module directly (not the full ``api_router``
    aggregator) so this test stays decoupled from every other route's
    dependency surface. Importing the aggregator would force-load admin /
    audit / documents modules whose own DI seams are out of scope here.
    """
    from ragbot.interfaces.http.routes import chat_async

    @asynccontextmanager
    async def _noop_lifespan(application: FastAPI) -> AsyncIterator[None]:
        container = MagicMock()

        bot_repo = MagicMock()
        bot_repo.find_by_4key = AsyncMock(return_value=bot_cfg)
        container.bot_repo = MagicMock(return_value=bot_repo)

        cfg_svc = MagicMock()
        cfg_svc.get_int = AsyncMock(return_value=system_max_tokens)
        container.system_config_service = MagicMock(return_value=cfg_svc)

        container.redis_client = MagicMock(return_value=fake_redis)

        application.state.container = container
        application.state.settings = MagicMock()
        yield

    app = FastAPI(lifespan=_noop_lifespan)
    app.include_router(chat_async.router, prefix="/api/ragbot/test")
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_post_returns_job_id_and_writes_stream() -> None:
    """POST → 200 + ``job_id`` + Stream entry with the worker-expected fields."""
    bot_uuid = uuid.uuid4()
    fake = _FakeRedis()
    app = _build_test_app(
        fake_redis=fake,
        bot_cfg=_stub_bot(
            bot_uuid=bot_uuid,
            tokens_used=10,
            extra_max_tokens=1_000,
        ),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/ragbot/test/chat-async",
            json={
                "bot_id": "support",
                "channel_type": "web",
                "workspace_id": "ws-acme",
                "question": "What is the refund policy?",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "pending"
    job_id = body["job_id"]
    # job_id must be a valid UUID — never echoed from the wire.
    uuid.UUID(job_id)

    # Stream must contain exactly one entry with the expected layout.
    entries = fake.streams.get(CHAT_REQUEST_STREAM, [])
    assert len(entries) == 1, "POST must XADD exactly one job"
    _msg_id, fields = entries[0]
    assert fields[b"job_id"].decode() == job_id
    payload = json.loads(fields[b"req"].decode())
    assert payload["bot_id"] == "support"
    assert payload["channel_type"] == "web"
    assert payload["workspace_id"] == "ws-acme"
    assert payload["question"] == "What is the refund policy?"
    assert payload["record_bot_id"] == str(bot_uuid)
    # tenant uuid must be present so the worker can scope the LangGraph state.
    uuid.UUID(payload["record_tenant_id"])


def test_post_refuses_quota_exhausted_with_db_template() -> None:
    """Quota gate refuses BEFORE enqueue; refusal text comes from DB column."""
    fake = _FakeRedis()
    bot_cfg = _stub_bot(
        bot_uuid=uuid.uuid4(),
        tokens_used=10_000,  # already over the cap
        extra_max_tokens=0,
        oos_template="[bot-defined refusal]",
    )
    app = _build_test_app(
        fake_redis=fake,
        bot_cfg=bot_cfg,
        system_max_tokens=100,  # very small budget so 10_000 is over
    )

    with TestClient(app) as client:
        resp = client.post(
            "/api/ragbot/test/chat-async",
            json={
                "bot_id": "support",
                "channel_type": "web",
                "question": "Q",
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["blocked"] is True
    assert body["blocked_reason"] == "QUOTA_EXHAUSTED"
    # CRITICAL: refusal text from DB column, NOT an app-injected i18n literal.
    assert body["answer"] == "[bot-defined refusal]"
    # Must NOT enqueue when refused.
    assert CHAT_REQUEST_STREAM not in fake.streams or not fake.streams[CHAT_REQUEST_STREAM]


def test_post_404_when_bot_not_found() -> None:
    """Unknown 4-key tuple → 404, no Stream write."""
    fake = _FakeRedis()
    app = _build_test_app(fake_redis=fake, bot_cfg=None)

    with TestClient(app) as client:
        resp = client.post(
            "/api/ragbot/test/chat-async",
            json={
                "bot_id": "ghost",
                "channel_type": "web",
                "question": "Q",
            },
        )

    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]
    assert CHAT_REQUEST_STREAM not in fake.streams or not fake.streams[CHAT_REQUEST_STREAM]


def test_get_pending_when_hash_absent() -> None:
    """Unknown ``job_id`` → ``{status: "pending"}`` (worker has not written yet)."""
    fake = _FakeRedis()
    app = _build_test_app(fake_redis=fake, bot_cfg=_stub_bot(bot_uuid=uuid.uuid4()))

    job_id = "00000000-0000-0000-0000-000000000abc"
    with TestClient(app) as client:
        resp = client.get(f"/api/ragbot/test/chat-async/{job_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["job_id"] == job_id
    # Must NOT leak an "answer" field when nothing has been written yet —
    # clients distinguish pending from done by the presence of "answer".
    assert "answer" not in body


def test_get_returns_done_envelope_when_worker_wrote_hash() -> None:
    """GET surfaces the worker's answer + citations + duration + chunks_used."""
    fake = _FakeRedis()
    app = _build_test_app(fake_redis=fake, bot_cfg=_stub_bot(bot_uuid=uuid.uuid4()))

    job_id = str(uuid.uuid4())
    citations_payload = [{"chunk_id": "c-7", "score": 0.88}]
    # Pre-seed the hash as the worker would after successful pipeline run.
    asyncio.new_event_loop().run_until_complete(
        fake.hset(
            f"{CHAT_RESULT_HASH_PREFIX}{job_id}",
            mapping={
                "status": "done",
                "answer": "Final answer body.",
                "citations": json.dumps(citations_payload),
                "chunks_used": "7",
                "duration_ms": "412",
            },
        ),
    )

    with TestClient(app) as client:
        resp = client.get(f"/api/ragbot/test/chat-async/{job_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "done"
    assert body["answer"] == "Final answer body."
    assert body["citations"] == citations_payload
    assert body["chunks_used"] == 7
    assert body["duration_ms"] == 412


def test_get_surfaces_worker_error_state() -> None:
    """Worker recorded ``status=error`` → GET returns the error message."""
    fake = _FakeRedis()
    app = _build_test_app(fake_redis=fake, bot_cfg=_stub_bot(bot_uuid=uuid.uuid4()))

    job_id = str(uuid.uuid4())
    asyncio.new_event_loop().run_until_complete(
        fake.hset(
            f"{CHAT_RESULT_HASH_PREFIX}{job_id}",
            mapping={
                "status": "error",
                "error": "RuntimeError: upstream LLM 503",
            },
        ),
    )

    with TestClient(app) as client:
        resp = client.get(f"/api/ragbot/test/chat-async/{job_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert "503" in body["error"]


def test_post_uses_constants_not_hardcoded_stream_name() -> None:
    """Sanity guard: route module must reference the SSoT constant, not a literal.

    A direct ``"chat.requested"`` literal in the route would drift from the
    worker's constant on rename — this test grep-asserts the import lives
    in the module so a future refactor cannot quietly bypass the SSoT.
    """
    import ragbot.interfaces.http.routes.chat_async as mod
    src = open(mod.__file__, encoding="utf-8").read()
    # Constants must be imported by name (single source of truth).
    assert "CHAT_REQUEST_STREAM" in src
    assert "CHAT_RESULT_HASH_PREFIX" in src
    # And referenced — not just imported then ignored.
    # The literal stream string may legitimately appear in the docstring;
    # the structural check above is the load-bearing one. We additionally
    # verify the worker stream name is NOT inlined inside an xadd call.
    assert 'xadd("chat.requested"' not in src
    assert "xadd('chat.requested'" not in src
