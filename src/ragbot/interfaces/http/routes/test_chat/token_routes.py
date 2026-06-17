"""JWT service-token CRUD routes for the test_chat package.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .schemas import CreateTokenRequest
from ._shared import (
    _audit_entry,
    _container,
    _require_owner,
    _token_service,
)

router = APIRouter(tags=["test"])


@router.post("/tokens")
async def create_token(req: CreateTokenRequest, request: Request) -> dict:
    """Tạo JWT token mới cho service, khởi tạo version=1.
    @param req: {service_name, description, role, rate_limit_rps}
    @return: {ok, id, service_name, token, version, role, rate_limit_rps}

    P0 — emit forensic audit row. NEVER include the plaintext
    JWT in ``after_json``: only the row id + role + rate-limit shape.
    """
    _require_owner(request)
    svc = await _token_service(request)
    redis = _container(request).redis_client()
    result = await svc.create_token(
        req.service_name, req.description, redis_client=redis,
        role=req.role, rate_limit_value=req.rate_limit_value,
        rate_limit_window=req.rate_limit_window,
    )
    audit_repo = _container(request).ai_config_repo()
    await audit_repo.write_audit(
        _audit_entry(
            request,
            action="token_create",
            resource_type="api_token",
            resource_id=result["id"],
            before=None,
            after={
                "service_name": req.service_name,
                "role": req.role,
                "rate_limit_value": result.get("rate_limit_value"),
                "rate_limit_window": result.get("rate_limit_window"),
                "version": result.get("version"),
            },
        ),
    )
    return {"ok": True, **result}


@router.post("/tokens/{service_name}/regenerate")
async def regenerate_token(service_name: str, request: Request) -> dict:
    """Tạo lại token — tăng version, token cũ bị vô hiệu.
    @param service_name: tên service
    @return: {ok, service_name, token, old_version, new_version, role, rate_limit_rps}

    P0 — emit forensic audit row (token rotation = security
    event). Plaintext JWT NEVER included.
    """
    _require_owner(request)
    svc = await _token_service(request)
    redis = _container(request).redis_client()
    try:
        result = await svc.regenerate_token(service_name, redis_client=redis)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    audit_repo = _container(request).ai_config_repo()
    await audit_repo.write_audit(
        _audit_entry(
            request,
            action="token_rotate",
            resource_type="api_token",
            resource_id=service_name,
            before={"version": result.get("old_version")},
            after={
                "service_name": service_name,
                "version": result.get("new_version"),
                "role": result.get("role"),
            },
        ),
    )
    return {"ok": True, **result}


@router.delete("/tokens/{service_name}")
async def revoke_token(service_name: str, request: Request) -> dict:
    """Thu hồi token — service không dùng được nữa.
    @param service_name: tên service
    @return: {ok, revoked}

    P0 — emit forensic audit row.
    """
    _require_owner(request)
    svc = await _token_service(request)
    redis = _container(request).redis_client()
    revoked = await svc.revoke_token(service_name, redis_client=redis)
    if not revoked:
        raise HTTPException(status_code=404, detail="Service not found or already revoked")
    audit_repo = _container(request).ai_config_repo()
    await audit_repo.write_audit(
        _audit_entry(
            request,
            action="token_revoke",
            resource_type="api_token",
            resource_id=service_name,
            before={"revoked": False},
            after={"revoked": True},
        ),
    )
    return {"ok": True, "revoked": True}


@router.get("/tokens")
async def list_tokens(request: Request) -> dict:
    """Liệt kê tất cả tokens (không trả token value).
    @return: {ok, tokens: [...]}
    """
    _require_owner(request)
    svc = await _token_service(request)
    tokens = await svc.list_tokens()
    return {"ok": True, "tokens": tokens}


__all__ = [
    "router",
    "create_token",
    "regenerate_token",
    "revoke_token",
    "list_tokens",
]
