"""Admin backend routes (config / api-keys / redis / models) for test_chat.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving).
READ-ONLY except the config + api-key mutations, which emit forensic audit rows.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from .schemas import UpdateConfigRequest, UpsertApiKeyRequest
from ._shared import (
    _audit_entry,
    _container,
    _require_owner,
    _sf,
    _sys_config,
)

router = APIRouter(tags=["test"])


@router.get("/admin/config")
async def admin_list_config(request: Request) -> dict:
    """Lấy toàn bộ cấu hình hệ thống (system_config).
    @return: {ok, configs: [{key, value, updated_at}, ...]}
    """
    _require_owner(request)
    svc = _sys_config(request)
    configs = await svc.get_all()
    return {"ok": True, "configs": configs}


@router.put("/admin/config/{key}")
async def admin_update_config(key: str, req: UpdateConfigRequest, request: Request) -> dict:
    """Cập nhật giá trị một cấu hình hệ thống.
    @param key: khóa cấu hình cần cập nhật
    @param req: {value: str} — giá trị mới
    @return: {ok, key, value}

    P0 — emit forensic ``audit_log`` row. ``system_config``
    drives every threshold + flag in the platform; mutations MUST be
    traceable per change-management policy.
    """
    _require_owner(request)
    svc = _sys_config(request)
    old_value = await svc.get(key)
    await svc.set(key, req.value)
    audit_repo = _container(request).ai_config_repo()
    await audit_repo.write_audit(
        _audit_entry(
            request,
            action="system_config_update",
            resource_type="system_config",
            resource_id=key,
            before={"value": old_value},
            after={"value": req.value},
        ),
    )
    # Hot-reload — drop the in-process bootstrap_config cache so DI
    # Factory providers (reranker, embedder, pii, ...) observe the new
    # value on the next request without restarting the worker.
    try:
        from ragbot.shared.bootstrap_config import invalidate_cache
        invalidate_cache(key)
    except ImportError:
        pass
    return {"ok": True, "key": key, "value": req.value}


@router.get("/admin/api-keys")
async def admin_list_api_keys(request: Request) -> dict:
    """List configured provider API keys (fingerprint only, never raw value).

    @return: {ok, keys: [{provider_code, label, fingerprint, active,
        rotation_state, updated_at}, ...]}
    """
    _require_owner(request)
    import hashlib

    from ragbot.shared.constants import API_KEY_FINGERPRINT_HEX_LEN
    sf = _container(request).session_factory()
    secrets = _container(request).secrets_port()
    from sqlalchemy import text as _sql_text
    async with sf() as session:
        result = await session.execute(
            _sql_text(
                """
                SELECT provider_code, label, value_encrypted, value_plain,
                       metadata_json->>'fingerprint' AS fingerprint, active,
                       rotation_state, updated_at
                FROM api_keys
                WHERE deleted_at IS NULL
                ORDER BY provider_code, label
                """,
            ),
        )
        keys = []
        for row in result.mappings():
            fp = row["fingerprint"]
            if not fp:
                # Migration-window fallback for rows written before the
                # fingerprint landed in metadata_json: derive it from the
                # stored key (admin-only path, low frequency). Drop the
                # value_plain branch at the ADR-W1-KEY kill-date.
                if row["value_encrypted"]:
                    raw = await secrets.resolve(None, row["value_encrypted"])
                elif row["value_plain"]:
                    raw = row["value_plain"]
                else:
                    raw = ""
                fp = (
                    hashlib.sha256(raw.encode()).hexdigest()[
                        :API_KEY_FINGERPRINT_HEX_LEN
                    ]
                    if raw
                    else "(empty)"
                )
            keys.append({
                "provider_code": row["provider_code"],
                "label": row["label"],
                "fingerprint": fp,
                "active": row["active"],
                "rotation_state": row["rotation_state"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            })
    return {"ok": True, "keys": keys}


@router.put("/admin/api-keys/{provider_code}")
async def admin_upsert_api_key(
    provider_code: str, req: UpsertApiKeyRequest, request: Request,
) -> dict:
    """Upsert an API key for ``provider_code`` + ``label``.

    Writes ``api_keys`` table + busts the Redis resolver cache so the
    next request reads the fresh value. No restart required.

    @return: {ok, provider_code, label, fingerprint}
    """
    _require_owner(request)
    if not req.value or not req.value.strip():
        raise HTTPException(status_code=400, detail="value must not be empty")
    from ragbot.application.services.provider_key_resolver import upsert_api_key
    sf = _container(request).session_factory()
    # Encrypt-and-upsert via SecretsPort (ADR-W1-KEY): value_encrypted only,
    # value_plain NULLed, fingerprint persisted in metadata_json. A missing
    # KEK raises before any SQL runs → 500, no plaintext row written.
    secrets = _container(request).secrets_port()
    async with sf() as session:
        fingerprint = await upsert_api_key(
            session, secrets, provider_code, req.label, req.value,
        )
        await session.commit()
    # Bust resolver cache.
    resolver = _container(request).provider_key_resolver()
    await resolver.invalidate(provider_code, req.label)
    # Audit trail.
    audit_repo = _container(request).ai_config_repo()
    await audit_repo.write_audit(
        _audit_entry(
            request,
            action="api_key_upsert",
            resource_type="api_key",
            resource_id=f"{provider_code}:{req.label}",
            before={},
            after={"fingerprint": fingerprint},
        ),
    )
    return {
        "ok": True,
        "provider_code": provider_code,
        "label": req.label,
        "fingerprint": fingerprint,
    }


@router.delete("/admin/api-keys/{provider_code}/{label}")
async def admin_delete_api_key(
    provider_code: str, label: str, request: Request,
) -> dict:
    """Soft-delete an API key. Cache busted so adapters fall back to env."""
    _require_owner(request)
    sf = _container(request).session_factory()
    from sqlalchemy import text as _sql_text
    async with sf() as session:
        await session.execute(
            _sql_text(
                """
                UPDATE api_keys
                SET deleted_at = now(),
                    active = false,
                    rotation_state = 'revoked',
                    updated_at = now()
                WHERE provider_code = :p
                  AND label = :l
                  AND deleted_at IS NULL
                """,
            ),
            {"p": provider_code, "l": label},
        )
        await session.commit()
    resolver = _container(request).provider_key_resolver()
    await resolver.invalidate(provider_code, label)
    audit_repo = _container(request).ai_config_repo()
    await audit_repo.write_audit(
        _audit_entry(
            request,
            action="api_key_delete",
            resource_type="api_key",
            resource_id=f"{provider_code}:{label}",
            before={"active": True},
            after={"active": False, "rotation_state": "revoked"},
        ),
    )
    return {"ok": True, "provider_code": provider_code, "label": label}


@router.get("/admin/redis/keys")
async def admin_redis_keys(request: Request) -> dict:
    """Liệt kê tất cả Redis keys theo pattern ragbot:* kèm type và TTL.
    @return: {ok, keys: [{key, type, ttl}, ...]}
    """
    _require_owner(request)
    redis = _container(request).redis_client()
    raw_keys = await redis.keys("ragbot:*")
    result = []
    for k in sorted(raw_keys):
        key_str = k.decode() if isinstance(k, bytes) else k
        key_type = await redis.type(key_str)
        if isinstance(key_type, bytes):
            key_type = key_type.decode()
        ttl = await redis.ttl(key_str)
        result.append({"key": key_str, "type": key_type, "ttl": ttl})
    return {"ok": True, "keys": result}


@router.get("/admin/redis/key/{key_path:path}")
async def admin_redis_key_detail(key_path: str, request: Request) -> dict:
    """Lấy giá trị của một Redis key cụ thể, hỗ trợ nhiều kiểu dữ liệu.
    @param key_path: đường dẫn key Redis (hỗ trợ dấu /)
    @return: {ok, key, type, value, ttl}
    """
    _require_owner(request)
    redis = _container(request).redis_client()
    key_type = await redis.type(key_path)
    if isinstance(key_type, bytes):
        key_type = key_type.decode()

    if key_type == "none":
        raise HTTPException(status_code=404, detail=f"Key not found: {key_path}")

    value: Any = None
    if key_type == "string":
        raw = await redis.get(key_path)
        value = raw.decode() if isinstance(raw, bytes) else raw
    elif key_type == "hash":
        raw = await redis.hgetall(key_path)
        value = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
    elif key_type == "list":
        raw = await redis.lrange(key_path, 0, -1)
        value = [item.decode() if isinstance(item, bytes) else item for item in raw]
    elif key_type == "set":
        raw = await redis.smembers(key_path)
        value = sorted(item.decode() if isinstance(item, bytes) else item for item in raw)
    elif key_type == "zset":
        raw = await redis.zrange(key_path, 0, -1, withscores=True)
        value = [
            {"member": m.decode() if isinstance(m, bytes) else m, "score": s}
            for m, s in raw
        ]
    else:
        value = f"(unsupported type: {key_type})"

    ttl = await redis.ttl(key_path)
    return {"ok": True, "key": key_path, "type": key_type, "value": value, "ttl": ttl}


@router.get("/admin/models")
async def admin_list_models(request: Request) -> dict:
    """Liệt kê tất cả AI models đang hoạt động kèm thông tin provider.

    Bug fix query was using stale column names (`m.display_name`,
    `m.purpose`, `m.is_default`, `m.is_active`, `m.provider_id`). Schema
    canonical (per migration 0034 rename + ORM models.py:386-400):
      - `m.name`             (not `display_name`)
      - `m.kind`             (not `purpose`)
      - `m.enabled`          (not `is_active`)
      - `m.record_provider_id` (not `provider_id`)
      - `m.deleted_at IS NULL` (soft-delete filter)
      - no `is_default` column → return None for that response field.

    @return: {ok, models: [{id, model_id, display_name, purpose, is_default,
        provider_name, base_url}, ...]}
    """
    _require_owner(request)
    sf = _sf(request)
    async with sf() as session:
        rows = (await session.execute(text("""
            SELECT m.id, m.model_id, m.name AS display_name, m.kind AS purpose,
                   p.name AS provider_name, p.base_url
            FROM ai_models m
            LEFT JOIN ai_providers p ON m.record_provider_id = p.id
            WHERE m.enabled = true
              AND m.deleted_at IS NULL
            ORDER BY m.kind, m.name
        """))).fetchall()
    return {
        "ok": True,
        "models": [
            {
                "id": str(r[0]), "model_id": r[1], "display_name": r[2],
                "purpose": r[3], "is_default": None,
                "provider_name": r[4], "base_url": r[5],
            }
            for r in rows
        ],
    }


__all__ = [
    "router",
    "admin_list_config",
    "admin_update_config",
    "admin_list_api_keys",
    "admin_upsert_api_key",
    "admin_delete_api_key",
    "admin_redis_keys",
    "admin_redis_key_detail",
    "admin_list_models",
]
