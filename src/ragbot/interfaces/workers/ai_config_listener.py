"""Consume ai.config.changed events → invalidate resolver cache + refresh router.

Run as: `python -m ragbot.interfaces.workers.ai_config_listener`
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any
from uuid import UUID

import structlog

from ragbot.application.services.system_config_service import (
    invalidate_local_cache as _invalidate_system_config_local,
)
from ragbot.bootstrap import Container
from ragbot.config.logging import setup_logging
from ragbot.config.settings import get_settings
from ragbot.shared.constants import (
    SUBJECT_SYSTEM_CONFIG_CHANGED,
    SUBJECT_TOKEN_REVOKED,
)

logger = structlog.get_logger(__name__)

# Cache prefix mirror — matches ``JwtTokenService._CACHE_PREFIX``.
# Listener-side inversion of the producer's private key prefix; defined
# here too so the listener doesn't import a private symbol.
_TOKEN_CACHE_PREFIX = "ragbot:token_ver:"

SUBJECTS: tuple[str, ...] = (
    "ai.config.changed.v1",
    "bot.config_updated.v1",
    "bot.registry.changed.v1",
    SUBJECT_SYSTEM_CONFIG_CHANGED,
    SUBJECT_TOKEN_REVOKED,
)


async def handle_config_changed(
    payload: dict[str, Any],
    container: Container,
    *,
    subject: str | None = None,
) -> None:
    """Invalidate resolver / bot-registry caches based on subject + payload."""
    event_type = payload.get("event_type") or payload.get("type") or subject
    bot_id = payload.get("bot_id")
    channel_type = payload.get("channel_type")
    workspace_id = payload.get("workspace_id")
    # Accept new UUID claim or legacy INT — resolver downstream needs UUID.
    tenant_id = (
        payload.get("record_tenant_id")
        or payload.get("tenant_uuid")
        or payload.get("tenant_id")
    )

    # Bug 2 (P0) — system_config cross-replica invalidation. Local
    # ``SystemConfigService.set`` only drops THIS replica's Redis key;
    # peers stay stale until the per-key TTL expires (5 min). The
    # outbox publisher fans this event out to every replica's
    # listener, which deletes the local cache so the next read falls
    # through to DB and converges.
    if event_type == SUBJECT_SYSTEM_CONFIG_CHANGED or subject == SUBJECT_SYSTEM_CONFIG_CHANGED:
        cfg_key = payload.get("key")
        if cfg_key and hasattr(container, "redis_client"):
            try:
                redis = container.redis_client()
                await _invalidate_system_config_local(redis, str(cfg_key))
            except Exception:  # noqa: BLE001 — listener resilience
                logger.exception("system_config_local_invalidate_failed", key=cfg_key)
        logger.info("system_config.invalidated", key=str(cfg_key) if cfg_key else None)
        return

    # Bug 5 (P1) — token revocation cross-replica invalidation.
    # ``JwtTokenService.revoke_token`` deletes the local Redis cache
    # entry but peer replicas keep the cached version → stale tokens
    # still validate for up to ``_CACHE_TTL`` seconds. The outbox
    # event drops the same key on every replica.
    if event_type == SUBJECT_TOKEN_REVOKED or subject == SUBJECT_TOKEN_REVOKED:
        service_name = payload.get("service_name")
        if service_name and hasattr(container, "redis_client"):
            try:
                redis = container.redis_client()
                await redis.delete(f"{_TOKEN_CACHE_PREFIX}{service_name}")
            except Exception:  # noqa: BLE001 — listener resilience
                logger.exception("token_revoke_local_invalidate_failed", service=service_name)
        logger.info(
            "token.revoked.invalidated",
            service=str(service_name) if service_name else None,
        )
        return

    # Bot registry invalidation (event-driven)
    if event_type == "bot.registry.changed.v1" or subject == "bot.registry.changed.v1":
        if hasattr(container, "bot_registry_service"):
            try:
                registry = container.bot_registry_service()
                # registry.invalidate requires the record_tenant_id UUID +
                # workspace slug. Legacy outbox events without a UUID
                # tenant or without a workspace slug cannot resolve
                # cleanly here so fall back to ``invalidate_all`` rather
                # than risk wrong-tenant or wrong-workspace eviction.
                tenant_uuid: UUID | None = None
                if tenant_id is not None:
                    try:
                        tenant_uuid = UUID(str(tenant_id))
                    except (TypeError, ValueError):
                        tenant_uuid = None
                if (
                    bot_id and channel_type
                    and tenant_uuid is not None
                    and workspace_id
                ):
                    await registry.invalidate(
                        tenant_uuid,
                        str(workspace_id),
                        str(bot_id),
                        str(channel_type),
                    )
                else:
                    await registry.invalidate_all()
            except Exception:  # noqa: BLE001 — background config listener; log + continue so subsequent events still process
                logger.exception("bot_registry_invalidate_failed")
        logger.info(
            "bot_registry.invalidated",
            tenant_id=str(tenant_id) if tenant_id is not None else None,
            workspace_id=str(workspace_id) if workspace_id else None,
            bot_id=str(bot_id) if bot_id else None,
            channel_type=str(channel_type) if channel_type else None,
        )
        return

    resolver = container.model_resolver()
    try:
        if tenant_id or bot_id:
            await resolver.invalidate(record_tenant_id=tenant_id, record_bot_id=bot_id)
        else:
            await resolver.invalidate_all()
    except AttributeError:  # pragma: no cover — pre Coder-2
        logger.warning("resolver_missing_invalidate_methods")

    # Refresh LLM router routing table if available.
    llm = container.llm() if hasattr(container, "llm") else None
    if llm is not None and hasattr(llm, "refresh_routing"):
        try:
            await llm.refresh_routing()
        except Exception:  # noqa: BLE001 — best-effort routing refresh; log + continue, next event will retry
            logger.exception("llm_refresh_routing_failed")

    logger.info(
        "ai_config.invalidated",
        tenant_id=str(tenant_id) if tenant_id else None,
        bot_id=str(bot_id) if bot_id else None,
    )


async def main() -> None:
    """Khởi chạy listener cho các sự kiện thay đổi cấu hình AI."""
    settings = get_settings()
    setup_logging(level=settings.observability.log_level, json=True)
    container = Container()
    bus = container.bus()

    if hasattr(bus, "ensure_streams"):
        await bus.ensure_streams()

    stop = asyncio.Event()

    def _make_handler(subject: str):  # noqa: ANN202
        async def _handler(event: Any) -> None:
            try:
                payload = getattr(event, "payload", None)
                if payload is None and hasattr(event, "to_dict"):
                    payload = event.to_dict()
                await handle_config_changed(
                    payload or {}, container, subject=subject,
                )
            except Exception:  # noqa: BLE001 — background event handler wrapper; isolate any failure so consumer stays subscribed
                logger.exception("ai_config_listener_error")
        return _handler

    subs = []
    for subject in SUBJECTS:
        sub = await bus.subscribe(
            subject,
            _make_handler(subject),
            durable_name=f"ai-config-{subject.replace('.', '-')}",
            queue_group="ai-config",
        )
        subs.append(sub)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    logger.info("ai_config_listener_started", subjects=list(SUBJECTS))
    await stop.wait()

    for sub in subs:
        try:
            await sub.unsubscribe()
        except Exception:  # noqa: BLE001 — shutdown cleanup; log + continue to drain remaining subs
            logger.exception("unsubscribe_failed")
    await bus.close()


if __name__ == "__main__":
    asyncio.run(main())
