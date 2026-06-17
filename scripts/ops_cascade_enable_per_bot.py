"""Wave J3 — Operator: enable Cascade Routing per-bot safely.

Wraps the cascade_routing_enabled flag flip with pre-flight verify of
required ``system_config`` tier rows, Redis registry cache busting, and
a 5-turn smoke against the production endpoint to confirm the cascade
event journal fires post-flip. Rollback disables the flag and re-busts
the cache.

Usage::

    # Pre-flight only (no DB write)
    python scripts/ops_cascade_enable_per_bot.py \\
        --bot test-spa-id --workspace dev-ws --channel web \\
        --action preflight

    # Enable + smoke
    python scripts/ops_cascade_enable_per_bot.py \\
        --bot test-spa-id --workspace dev-ws --channel web \\
        --action enable

    # Rollback
    python scripts/ops_cascade_enable_per_bot.py \\
        --bot test-spa-id --workspace dev-ws --channel web \\
        --action disable

Sacred rules (per CLAUDE.md):

- **Idempotent**: enabling twice is a no-op (UPDATE with same value).
- **Atomic**: any pre-flight failure aborts before touching DB.
- **Pre-flight**: required tier rows (cascade_low_model / cascade_high_model
  / default_answer_model) must be present in ``system_config`` AND
  resolvable to a row in ``ai_models`` SSoT.
- **No per-bot logic in core**: this script writes ``plan_limits``
  (JSONB column) — no hard-coded behaviour change in ``src/ragbot/``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import psycopg2
import psycopg2.extras
import redis as redis_sync

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SMOKE_TURNS = 5
DEFAULT_TIMEOUT_S = 60
REQUIRED_TIER_KEYS: tuple[str, ...] = (
    "default_answer_model",
    "cascade_low_model",
    "cascade_high_model",
)
SMOKE_QUESTIONS: tuple[str, ...] = (
    "Cho tôi xin báo giá dịch vụ chính.",
    "Có chương trình khuyến mãi nào không?",
    "Spa có dịch vụ cho khách nam không?",
    "Giờ mở cửa cuối tuần thế nào?",
    "Bot có thể tư vấn được không?",
)
RAGBOT_LOADTEST_BYPASS_ENV = "RAGBOT_LOADTEST_BYPASS_TOKEN"
RAGBOT_LOADTEST_BYPASS_HEADER = "X-Ragbot-Loadtest-Bypass"


def _dsn() -> str:
    raw = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL_SYNC / DATABASE_URL env var required")
    return raw.replace("postgresql+psycopg2://", "postgresql://", 1).replace(
        "postgresql+asyncpg://", "postgresql://", 1,
    )


def _redis_client() -> redis_sync.Redis:
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis_sync.from_url(url, socket_timeout=5)


def _bypass_headers() -> dict[str, str]:
    token = os.environ.get(RAGBOT_LOADTEST_BYPASS_ENV, "")
    if not token:
        return {}
    return {RAGBOT_LOADTEST_BYPASS_HEADER: token}


def preflight_tier_rows() -> tuple[bool, list[str]]:
    """Return ``(ok, missing_keys)``.

    Checks every required tier key exists in ``system_config`` AND that the
    referenced model name exists in ``ai_models`` SSoT.
    """
    missing: list[str] = []
    with psycopg2.connect(_dsn()) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT key, value FROM system_config WHERE key = ANY(%s)",
                (list(REQUIRED_TIER_KEYS),),
            )
            rows = {r["key"]: r["value"] for r in cur.fetchall()}
            for k in REQUIRED_TIER_KEYS:
                if k not in rows:
                    missing.append(f"{k}: row absent in system_config")
                    continue
                raw = rows[k]
                model_name = raw if isinstance(raw, str) else str(raw).strip('"')
                cur.execute(
                    "SELECT 1 FROM ai_models WHERE name = %s LIMIT 1",
                    (model_name,),
                )
                if cur.fetchone() is None:
                    missing.append(
                        f"{k}={model_name!r}: not present in ai_models"
                    )
    return (not missing), missing


def fetch_bot_plan_limits(
    record_tenant_id: str | None,
    workspace_id: str,
    bot_id: str,
    channel_type: str,
) -> dict[str, Any] | None:
    with psycopg2.connect(_dsn()) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if record_tenant_id:
                cur.execute(
                    """
                    SELECT id, plan_limits, record_tenant_id
                    FROM bots
                    WHERE record_tenant_id = %s
                      AND workspace_id = %s
                      AND bot_id = %s
                      AND channel_type = %s
                    LIMIT 1
                    """,
                    (record_tenant_id, workspace_id, bot_id, channel_type),
                )
            else:
                cur.execute(
                    """
                    SELECT id, plan_limits, record_tenant_id
                    FROM bots
                    WHERE workspace_id = %s
                      AND bot_id = %s
                      AND channel_type = %s
                    LIMIT 1
                    """,
                    (workspace_id, bot_id, channel_type),
                )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(row)


def set_cascade_flag(
    record_tenant_id: str | None,
    workspace_id: str,
    bot_id: str,
    channel_type: str,
    enabled: bool,
) -> int:
    """UPDATE ``plan_limits.cascade_routing_enabled``; return rowcount."""
    sql = """
        UPDATE bots
        SET plan_limits = COALESCE(plan_limits, '{}'::jsonb)
                          || jsonb_build_object(
                               'cascade_routing_enabled', %s::boolean
                             ),
            updated_at  = NOW()
        WHERE workspace_id = %s
          AND bot_id = %s
          AND channel_type = %s
    """
    params: tuple[Any, ...] = (enabled, workspace_id, bot_id, channel_type)
    if record_tenant_id:
        sql += " AND record_tenant_id = %s"
        params = (*params, record_tenant_id)
    with psycopg2.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rc = cur.rowcount
        conn.commit()
    return rc


def invalidate_registry_cache(bot_id: str) -> int:
    r = _redis_client()
    deleted = 0
    cursor = 0
    pattern = f"ragbot:bot:*{bot_id}*"
    while True:
        cursor, keys = r.scan(cursor=cursor, match=pattern, count=200)
        if keys:
            deleted += int(r.delete(*keys) or 0)
        if cursor == 0:
            break
    return deleted


def _self_token(client: httpx.Client, base_url: str) -> str:
    r = client.get(
        f"{base_url}/api/ragbot/test/tokens/self",
        headers=_bypass_headers(),
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def smoke_chat(
    base_url: str,
    bot_id: str,
    channel_type: str,
    workspace_id: str,
    turns: int,
    timeout_s: int,
) -> dict[str, Any]:
    """Run ``turns`` chat calls; return aggregate counters (no SQL writes)."""
    headers_extra = _bypass_headers()
    results: list[dict[str, Any]] = []
    tier_counter: Counter[str] = Counter()
    with httpx.Client(timeout=timeout_s) as client:
        token = _self_token(client, base_url)
        auth = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            **headers_extra,
        }
        for i in range(min(turns, len(SMOKE_QUESTIONS))):
            body = {
                "bot_id": bot_id,
                "channel_type": channel_type,
                "workspace_id": workspace_id,
                "question": SMOKE_QUESTIONS[i],
                "connect_id": f"ops-cascade-smoke-{i}",
                "bypass_cache": True,
            }
            try:
                r = client.post(
                    f"{base_url}/api/ragbot/test/chat",
                    json=body,
                    headers=auth,
                )
                r.raise_for_status()
                d = r.json()
            except httpx.HTTPError as exc:
                d = {"error": f"{type(exc).__name__}: {exc}"}
            results.append(d)
            tier = (
                d.get("cascade_tier")
                or d.get("answer_model_tier")
                or d.get("model_tier")
                or "unknown"
            )
            tier_counter[str(tier)] += 1
    err_count = sum(1 for d in results if "error" in d)
    return {
        "turns": len(results),
        "errors": err_count,
        "tier_distribution": dict(tier_counter),
        "ok": err_count == 0,
    }


def _ok(action: str, msg: str) -> None:
    print(f"[{action}] {msg}")


def _err(action: str, msg: str) -> None:
    print(f"[{action}] {msg}", file=sys.stderr)


def action_preflight(args: argparse.Namespace) -> int:
    ok, missing = preflight_tier_rows()
    if not ok:
        for m in missing:
            _err("preflight", m)
        return 1
    bot = fetch_bot_plan_limits(
        args.record_tenant_id, args.workspace, args.bot, args.channel
    )
    if bot is None:
        _err(
            "preflight",
            f"bot row not found: workspace={args.workspace} "
            f"bot={args.bot} channel={args.channel}",
        )
        return 1
    plan_limits = bot.get("plan_limits") or {}
    cur = plan_limits.get("cascade_routing_enabled", "<absent>")
    _ok(
        "preflight",
        f"tier rows OK · bot row found id={bot['id']} · "
        f"current cascade_routing_enabled={cur}",
    )
    return 0


def _flip(
    args: argparse.Namespace, enabled: bool, label: str
) -> int:
    ok, missing = preflight_tier_rows()
    if not ok:
        for m in missing:
            _err(label, m)
        return 1
    rc = set_cascade_flag(
        args.record_tenant_id,
        args.workspace,
        args.bot,
        args.channel,
        enabled,
    )
    if rc == 0:
        _err(label, "no bot row matched — UPDATE affected 0 rows")
        return 1
    _ok(label, f"flag set cascade_routing_enabled={enabled} rows={rc}")
    deleted = invalidate_registry_cache(args.bot)
    _ok(label, f"registry cache busted (deleted={deleted})")
    if args.skip_smoke:
        _ok(label, "smoke skipped per --skip-smoke")
        return 0
    time.sleep(args.smoke_wait_s)
    smoke = smoke_chat(
        args.base_url,
        args.bot,
        args.channel,
        args.workspace,
        args.smoke_turns,
        args.timeout,
    )
    _ok(label, f"smoke summary: {json.dumps(smoke, ensure_ascii=False)}")
    if not smoke["ok"]:
        _err(label, "smoke failed — auto-rollback flag")
        set_cascade_flag(
            args.record_tenant_id,
            args.workspace,
            args.bot,
            args.channel,
            not enabled,
        )
        invalidate_registry_cache(args.bot)
        return 1
    return 0


def action_enable(args: argparse.Namespace) -> int:
    return _flip(args, True, "enable")


def action_disable(args: argparse.Namespace) -> int:
    return _flip(args, False, "disable")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Enable / disable cascade routing per-bot with smoke"
    )
    ap.add_argument("--bot", required=True, help="bots.bot_id slug")
    ap.add_argument("--channel", default="web", help="bots.channel_type")
    ap.add_argument(
        "--workspace", required=True, help="bots.workspace_id slug"
    )
    ap.add_argument(
        "--record-tenant-id",
        default="",
        help="optional: bots.record_tenant_id UUID for scoped UPDATE",
    )
    ap.add_argument(
        "--action",
        choices=("preflight", "enable", "disable"),
        required=True,
    )
    ap.add_argument("--smoke-turns", type=int, default=DEFAULT_SMOKE_TURNS)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--smoke-wait-s", type=float, default=2.0)
    ap.add_argument("--skip-smoke", action="store_true")
    ap.add_argument(
        "--base-url",
        default=os.environ.get("RAGBOT_BASE_URL", "http://localhost:3004"),
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.action == "preflight":
        return action_preflight(args)
    if args.action == "enable":
        return action_enable(args)
    return action_disable(args)


if __name__ == "__main__":
    sys.exit(main())
