"""Load test for ragbot — REAL measurements via Locust.

Domain-neutral: bot identity (tenant_id, bot_id, channel_type) read from
env vars. Token from RAGBOT_TOKEN env. Questions read from golden_set
(absolute path). No tenant literals embedded.

Usage:
    export RAGBOT_TOKEN=$(curl -s 'http://localhost:3004/api/ragbot/test/tokens/self' \
        | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["token"])')
    export RAGBOT_LOAD_TENANT_ID=32
    export RAGBOT_LOAD_BOT_ID=thula-test-bot-v1
    export RAGBOT_LOAD_CHANNEL=web

    locust -f scripts/load_test/locustfile.py --headless -u 1 -r 1 -t 60s \
        --host http://localhost:3004 \
        --csv reports/load_test_smoke
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

from locust import HttpUser, between, events, task

# ------------------------------------------------------------------
# Configuration (env-driven, no tenant literals in code)
# ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
QUESTIONS_FILE = PROJECT_ROOT / "golden_set" / "kich_ban_questions_v1.json"

TOKEN = os.environ.get("RAGBOT_TOKEN")
TENANT_ID = int(os.environ.get("RAGBOT_LOAD_TENANT_ID", "0"))
BOT_ID = os.environ.get("RAGBOT_LOAD_BOT_ID", "")
CHANNEL = os.environ.get("RAGBOT_LOAD_CHANNEL", "web")
USER_ID = os.environ.get("RAGBOT_LOAD_USER_ID", "load-test-user")
REQUEST_TIMEOUT_S = float(os.environ.get("RAGBOT_LOAD_TIMEOUT_S", "60"))

# ------------------------------------------------------------------
# Question pool
# ------------------------------------------------------------------
def _load_questions() -> list[str]:
    if not QUESTIONS_FILE.exists():
        return ["Xin chao", "Co dich vu gi khong", "Gia bao nhieu"]
    data = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    items = data.get("questions") if isinstance(data, dict) else data
    out: list[str] = []
    for item in items or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            q = item.get("question") or item.get("q") or item.get("text")
            if isinstance(q, str) and q.strip():
                out.append(q.strip())
    return out or ["Xin chao"]


QUESTIONS = _load_questions()


# ------------------------------------------------------------------
# Sanity guard — refuse to run with missing identity
# ------------------------------------------------------------------
@events.test_start.add_listener
def _on_test_start(environment, **_kwargs) -> None:
    missing: list[str] = []
    if not TOKEN:
        missing.append("RAGBOT_TOKEN")
    if not TENANT_ID:
        missing.append("RAGBOT_LOAD_TENANT_ID")
    if not BOT_ID:
        missing.append("RAGBOT_LOAD_BOT_ID")
    if missing:
        environment.runner.quit()
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")
    print(
        f"[load-test] target tenant_id={TENANT_ID} bot_id={BOT_ID} "
        f"channel={CHANNEL} questions={len(QUESTIONS)}"
    )


# ------------------------------------------------------------------
# Scenario A/B/C — sync chat
# ------------------------------------------------------------------
class ChatSyncUser(HttpUser):
    """Hits POST /api/ragbot/test/chat with random questions."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.headers.update({
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        })

    @task
    def chat_sync(self) -> None:
        question = random.choice(QUESTIONS)
        payload = {
            "tenant_id": TENANT_ID,
            "bot_id": BOT_ID,
            "channel_type": CHANNEL,
            "question": question,
            "user_id": USER_ID,
        }
        with self.client.post(
            "/api/ragbot/test/chat",
            json=payload,
            timeout=REQUEST_TIMEOUT_S,
            name="POST /test/chat",
            catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"status={resp.status_code} body={resp.text[:200]}")
                return
            try:
                body = resp.json()
            except (ValueError, json.JSONDecodeError):
                resp.failure("non-json")
                return
            if not body.get("ok"):
                resp.failure(f"ok=false answer_type={body.get('answer_type')}")


# ------------------------------------------------------------------
# Scenario D — streaming (TTFT measurement)
# ------------------------------------------------------------------
class ChatStreamUser(HttpUser):
    """Hits POST /api/ragbot/test/chat/stream — measures TTFT custom metric."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        self.client.headers.update({
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        })

    @task
    def chat_stream(self) -> None:
        question = random.choice(QUESTIONS)
        payload = {
            "tenant_id": TENANT_ID,
            "bot_id": BOT_ID,
            "channel_type": CHANNEL,
            "question": question,
            "user_id": USER_ID,
        }
        t0 = time.perf_counter()
        ttft_ms: float | None = None
        ok = False
        bytes_recv = 0
        try:
            with self.client.post(
                "/api/ragbot/test/chat/stream",
                json=payload,
                timeout=REQUEST_TIMEOUT_S,
                name="POST /test/chat/stream",
                stream=True,
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"status={resp.status_code} body={resp.text[:200]}")
                    return
                for chunk in resp.iter_content(chunk_size=64):
                    if not chunk:
                        continue
                    if ttft_ms is None:
                        ttft_ms = (time.perf_counter() - t0) * 1000.0
                    bytes_recv += len(chunk)
                ok = True
                resp.success()
        finally:
            if ok and ttft_ms is not None:
                events.request.fire(
                    request_type="STREAM",
                    name="TTFT /test/chat/stream",
                    response_time=ttft_ms,
                    response_length=bytes_recv,
                    exception=None,
                    context={},
                )


# ------------------------------------------------------------------
# Scenario sanity — health check
# ------------------------------------------------------------------
class HealthUser(HttpUser):
    """Used optionally to smoke-test /health."""

    wait_time = between(2, 5)
    weight = 0  # disabled by default; set --user-classes to enable

    @task
    def health(self) -> None:
        self.client.get("/health", name="GET /health", timeout=5)
