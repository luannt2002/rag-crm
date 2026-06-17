"""AI config service — providers, models, bindings, cache, audit.

Extracted from admin_ai routes to follow hexagonal architecture.
Routes call this service; service orchestrates repo, resolver, audit, outbox.
"""

from __future__ import annotations

import datetime
import os
import time
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog

from ragbot.application.ports.ai_config_port import (
    AuditEntry,
    BindingRow,
)
from ragbot.domain.events.document_events import BotConfigUpdated
from ragbot.infrastructure.db.models import AIModelModel, AIProviderModel
from ragbot.shared.constants import (
    DEFAULT_AUDIT_LIST_LIMIT,
    DEFAULT_HTTP_SHORT_TIMEOUT_S,
    DEFAULT_JINA_API_BASE_URL,
    DEFAULT_JINA_RERANKER_MODEL,
    DEFAULT_KEY_VERIFY_TIMEOUT_S,
)
from ragbot.shared.errors import RepositoryError
from ragbot.shared.types import BotId, TenantId, TraceId

logger = structlog.get_logger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _provider_dict(p: object) -> dict[str, object]:
    return {
        "id": str(p.id),  # type: ignore[attr-defined]
        "name": p.name,  # type: ignore[attr-defined]
        "type": p.type,  # type: ignore[attr-defined]
        "base_url": p.base_url,  # type: ignore[attr-defined]
        "enabled": p.enabled,  # type: ignore[attr-defined]
    }


def _model_dict(m: object) -> dict[str, object]:
    return {
        "id": str(m.id),  # type: ignore[attr-defined]
        "name": m.name,  # type: ignore[attr-defined]
        "kind": m.kind,  # type: ignore[attr-defined]
        "provider_id": str(m.provider_id),  # type: ignore[attr-defined]
        "enabled": m.enabled,  # type: ignore[attr-defined]
    }


def _binding_dict(b: object) -> dict[str, object]:
    return {
        "id": str(b.id),  # type: ignore[attr-defined]
        "purpose": b.purpose,  # type: ignore[attr-defined]
        "model_id": str(b.model_id),  # type: ignore[attr-defined]
        "rank": b.rank,  # type: ignore[attr-defined]
        "variant": b.variant,  # type: ignore[attr-defined]
        "weight": b.weight,  # type: ignore[attr-defined]
        "active": b.active,  # type: ignore[attr-defined]
        "version": b.version,  # type: ignore[attr-defined]
    }


# ── Errors ─────────────────────────────────────────────────────────────────

class ProviderNotFoundError(Exception):
    pass


class ModelNotFoundError(Exception):
    pass


class BindingNotFoundError(Exception):
    pass


class BindingBotIdMismatchError(Exception):
    pass


class ModelDeleteConflictError(Exception):
    pass


class KeyNotFoundError(Exception):
    pass


class KeyVerifyFailedError(Exception):
    """Raised when verify_first=True and upstream rejects the key (4xx)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"key verify failed: HTTP {status_code} — {detail}")
        self.status_code = status_code
        self.detail = detail


# ── Provider-verify helpers ────────────────────────────────────────────────

async def _curl_verify_jina_rerank(
    api_key: str,
    base_url: str | None = None,
) -> tuple[bool, int, float, str]:
    """Test Jina rerank with 1 dummy doc. Return (ok, status_code, latency_ms, detail)."""
    endpoint = (base_url or os.getenv("JINA_API_BASE_URL", DEFAULT_JINA_API_BASE_URL)).rstrip("/")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=DEFAULT_KEY_VERIFY_TIMEOUT_S) as c:
        try:
            resp = await c.post(
                f"{endpoint}/rerank",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": DEFAULT_JINA_RERANKER_MODEL,
                    "query": "test",
                    "top_n": 1,
                    "documents": ["ping"],
                },
            )
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            if resp.status_code == 200:
                return True, 200, elapsed_ms, "ok"
            try:
                body = resp.json()
                detail = body.get("detail", str(resp.status_code))
                code = body.get("code", "")
                if "INSUFFICIENT_BALANCE" in code:
                    return False, resp.status_code, elapsed_ms, "quota_empty"
                return False, resp.status_code, elapsed_ms, detail[:80]
            except (ValueError, AttributeError):
                # Non-JSON 4xx body (HTML error page, plain text) → surface
                # the raw text snippet so the operator still has context.
                return False, resp.status_code, elapsed_ms, resp.text[:80]
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            return False, 0, (time.monotonic() - t0) * 1000.0, str(exc)[:80]


_VerifyFn = "Callable[[str, str | None], Awaitable[tuple[bool, int, float, str]]]"


async def _skip_verify(
    api_key: str,  # noqa: ARG001 — signature parity for registry dispatch
    base_url: str | None = None,  # noqa: ARG001
) -> tuple[bool, int, float, str]:
    """No-op verifier for providers without a verify endpoint wired."""
    return True, 0, 0.0, "skip_unsupported_provider"


# Registry of provider-specific key verifiers. Add a new provider by
# registering its verifier here — no edits to _verify_provider_key needed.
# Per CLAUDE.md Strategy + DI rule: dispatch via registry, not if/elif.
_KEY_VERIFY_REGISTRY: dict[str, Any] = {
    "jina_ai": lambda key, base_url: _curl_verify_jina_rerank(key, base_url=base_url),
}


async def _verify_provider_key(
    provider_code: str,
    api_key: str,
    base_url: str | None = None,
) -> tuple[bool, int, float, str]:
    """Dispatch verify to provider-specific endpoint via registry."""
    verifier = _KEY_VERIFY_REGISTRY.get(provider_code)
    if verifier is None:
        return await _skip_verify(api_key, base_url)
    return await verifier(api_key, base_url)


def _make_fingerprint(plain_key: str) -> str:
    """Build display fingerprint: first 8 chars + '...' + last 4 chars."""
    if len(plain_key) <= 12:  # noqa: PLR2004 — structural boundary, not magic
        return plain_key[:4] + "..."
    return plain_key[:8] + "..." + plain_key[-4:]


# ── Service ────────────────────────────────────────────────────────────────

class AIConfigService:
    """Manage AI providers, models, bindings, and cache."""

    def __init__(
        self,
        ai_config_repo: Any,
        model_resolver: Any,
        uow_factory: Any,
        session_factory: Any,
    ) -> None:
        self._repo = ai_config_repo
        self._resolver = model_resolver
        self._uow_factory = uow_factory
        self._session_factory = session_factory

    # ── Providers ──────────────────────────────────────────────────────────

    async def list_providers(self) -> list[dict[str, object]]:
        rows = await self._repo.list_providers(enabled_only=False)
        return [_provider_dict(p) for p in rows]

    async def create_provider(
        self,
        *,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
        name: str,
        type: str,
        base_url: str,
        auth_type: str,
        enabled: bool,
    ) -> dict[str, object]:
        async with self._session_factory() as session:
            row = AIProviderModel(
                id=uuid4(),
                name=name,
                type=type,
                base_url=base_url,
                auth_type=auth_type,
                enabled=enabled,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)

        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="create",
                resource_type="provider",
                resource_id=row.id,
                before=None,
                after={"name": name, "type": type},
                reason=None,
                trace_id=trace_id,
            ),
        )
        return _provider_dict(row)

    async def update_provider(
        self,
        *,
        provider_id: UUID,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
        fields: dict[str, Any],
    ) -> dict[str, object]:
        before = await self._repo.get_provider(provider_id)
        if before is None:
            raise ProviderNotFoundError("provider not found")
        updated = await self._repo.update_provider(provider_id, **fields)
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="update",
                resource_type="provider",
                resource_id=provider_id,
                before=_provider_dict(before),
                after=_provider_dict(updated),
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate_all()
        return _provider_dict(updated)

    async def delete_provider(
        self,
        *,
        provider_id: UUID,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
    ) -> None:
        before = await self._repo.get_provider(provider_id)
        if before is None:
            raise ProviderNotFoundError("provider not found")
        await self._repo.delete_provider(provider_id)
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="delete",
                resource_type="provider",
                resource_id=provider_id,
                before=_provider_dict(before),
                after=None,
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate_all()

    async def test_provider(self, provider_id: UUID) -> dict[str, object]:
        prov = await self._repo.get_provider(provider_id)
        if prov is None:
            raise ProviderNotFoundError("provider not found")
        healthcheck = (
            getattr(prov, "metadata", {}).get("healthcheck_url")
            if hasattr(prov, "metadata")
            else None
        )
        if not healthcheck:
            return {"ok": True, "note": "no healthcheck_url, skip"}
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_HTTP_SHORT_TIMEOUT_S) as c:
                r = await c.get(healthcheck)
                return {
                    "ok": True,
                    "status_code": r.status_code,
                    "latency_ms": r.elapsed.total_seconds() * 1000,
                }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def rotate_key(
        self,
        *,
        provider_id: UUID,
        plain_key: str,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
    ) -> None:
        prov = await self._repo.get_provider(provider_id)
        if prov is None:
            raise ProviderNotFoundError("provider not found")
        current_metadata = dict(getattr(prov, "metadata", None) or {})
        try:
            from ragbot.infrastructure.security.env_secrets import EnvSecretsAdapter

            encrypted = EnvSecretsAdapter.encrypt(plain_key)
            current_metadata["api_key_encrypted"] = encrypted
            current_metadata.pop("rotate_pending", None)
            await self._repo.update_provider(
                provider_id, metadata_json=current_metadata,
            )
        except (ImportError, AttributeError, RuntimeError):
            current_metadata["rotate_pending"] = True
            await self._repo.update_provider(
                provider_id, metadata_json=current_metadata,
            )
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="rotate_key",
                resource_type="provider",
                resource_id=provider_id,
                before=None,
                after={"api_key_encrypted": "***updated***"},
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate_all()

    # ── AI Key management (ai_keys table, alembic 0066) ────────────────────

    async def add_key(
        self,
        *,
        provider_id: UUID,
        plain_key: str,
        set_as_default: bool,
        verify_first: bool,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """Add a new API key to ai_keys.

        When verify_first=True the key is tested against the provider endpoint
        before persisting; a 4xx response raises KeyVerifyFailedError.
        When set_as_default=True the current default key is demoted to
        status='rotated_out' and the new key becomes is_default=true.
        """
        prov = await self._repo.get_provider(provider_id)
        if prov is None:
            raise ProviderNotFoundError("provider not found")

        verified_latency_ms: float | None = None
        if verify_first:
            ok, status_code, latency_ms, detail = await _verify_provider_key(
                prov.code, plain_key, base_url=getattr(prov, "base_url", None),
            )
            if not ok:
                raise KeyVerifyFailedError(status_code, detail)
            verified_latency_ms = latency_ms

        from ragbot.infrastructure.security.env_secrets import EnvSecretsAdapter
        encrypted = EnvSecretsAdapter.encrypt(plain_key)
        fingerprint = _make_fingerprint(plain_key)

        # Demote current default before insert to respect unique constraint.
        if set_as_default:
            existing_keys = await self._repo.list_keys(provider_id)
            for k in existing_keys:
                if k.get("is_default"):
                    await self._repo.mark_key_rotated_out(k["id"])

        key_id = await self._repo.insert_key(
            provider_id=provider_id,
            api_key_encrypted=encrypted,
            fingerprint=fingerprint,
            is_default=set_as_default,
        )

        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="add_key",
                resource_type="ai_key",
                resource_id=key_id,
                before=None,
                after={
                    "fingerprint": fingerprint,
                    "is_default": set_as_default,
                    "verified_latency_ms": verified_latency_ms,
                },
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate_all()
        return {
            "ok": True,
            "key_id": str(key_id),
            "fingerprint": fingerprint,
            "verified_latency_ms": verified_latency_ms,
        }

    async def verify_key(
        self,
        *,
        provider_id: UUID,
        key_id: UUID,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        """Re-test an existing key and update its health columns.

        Useful for re-checking a 403/quota_empty key after balance top-up.
        """
        prov = await self._repo.get_provider(provider_id)
        if prov is None:
            raise ProviderNotFoundError("provider not found")

        key_row = await self._repo.get_key(key_id)
        if key_row is None:
            raise KeyNotFoundError("key not found")

        from ragbot.infrastructure.security.env_secrets import EnvSecretsAdapter
        adapter = EnvSecretsAdapter()
        plain_key = await adapter.resolve(None, key_row["api_key_encrypted"])

        ok, status_code, latency_ms, balance_status = await _verify_provider_key(
            prov.code, plain_key, base_url=getattr(prov, "base_url", None),
        )

        await self._repo.update_key_health(
            key_id, status_code=status_code, latency_ms=latency_ms,
        )
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="verify_key",
                resource_type="ai_key",
                resource_id=key_id,
                before=None,
                after={
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "balance_status": balance_status,
                },
                reason=None,
                trace_id=trace_id,
            ),
        )
        return {
            "ok": ok,
            "status_code": status_code,
            "latency_ms": latency_ms,
            "balance_status": balance_status,
        }

    async def list_keys(self, *, provider_id: UUID) -> list[dict[str, Any]]:
        """Return all keys for provider, masked — never plain_key in response."""
        return await self._repo.list_keys(provider_id)

    # ── Models ─────────────────────────────────────────────────────────────

    async def list_models(
        self,
        *,
        provider_id: UUID | None = None,
        kind: str | None = None,
    ) -> list[dict[str, object]]:
        rows = await self._repo.list_models(
            provider_id=provider_id, kind=kind, enabled_only=False,
        )
        return [_model_dict(m) for m in rows]

    async def create_model(
        self,
        *,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
        provider_id: UUID,
        name: str,
        kind: str,
        context_window: int,
        max_output_tokens: int,
        input_price_per_1k_usd: float,
        output_price_per_1k_usd: float,
        supports_streaming: bool,
        supports_tools: bool,
        supports_vision: bool,
        supports_json_mode: bool,
        languages: list[str],
    ) -> dict[str, object]:
        async with self._session_factory() as session:
            row = AIModelModel(
                id=uuid4(),
                provider_id=provider_id,
                name=name,
                kind=kind,
                context_window=context_window,
                max_output_tokens=max_output_tokens,
                input_price_per_1k_usd=Decimal(str(input_price_per_1k_usd)),
                output_price_per_1k_usd=Decimal(str(output_price_per_1k_usd)),
                supports_streaming=supports_streaming,
                supports_tools=supports_tools,
                supports_vision=supports_vision,
                supports_json_mode=supports_json_mode,
                languages=list(languages),
                enabled=True,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)

        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="create",
                resource_type="model",
                resource_id=row.id,
                before=None,
                after={"name": name, "kind": kind, "provider_id": str(provider_id)},
                reason=None,
                trace_id=trace_id,
            ),
        )
        return _model_dict(row)

    async def update_model(
        self,
        *,
        model_id: UUID,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
        fields: dict[str, Any],
    ) -> dict[str, object]:
        before = await self._repo.get_model(model_id)
        if before is None:
            raise ModelNotFoundError("model not found")
        updated = await self._repo.update_model(model_id, **fields)
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="update",
                resource_type="model",
                resource_id=model_id,
                before=_model_dict(before),
                after=_model_dict(updated),
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate_all()
        return _model_dict(updated)

    async def delete_model(
        self,
        *,
        model_id: UUID,
        record_tenant_id: UUID | None,
        actor_user_id: str,
        trace_id: str,
    ) -> None:
        before = await self._repo.get_model(model_id)
        if before is None:
            raise ModelNotFoundError("model not found")
        try:
            await self._repo.delete_model(model_id)
        except Exception as exc:  # noqa: BLE001
            raise ModelDeleteConflictError(str(exc)) from exc
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=None,
                actor_user_id=actor_user_id,
                action="delete",
                resource_type="model",
                resource_id=model_id,
                before=_model_dict(before),
                after=None,
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate_all()

    # ── Bindings ───────────────────────────────────────────────────────────

    async def list_bindings(
        self, *, record_tenant_id: TenantId, record_bot_id: BotId,
    ) -> list[dict[str, object]]:
        rows = await self._repo.list_bindings(
            record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, active_only=False,
        )
        return [_binding_dict(b) for b in rows]

    async def create_binding(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        actor_user_id: str,
        trace_id: str,
        purpose: str,
        model_id: UUID,
        rank: int,
        variant: str | None,
        weight: int,
        temperature: float,
        max_tokens: int,
        top_p: float,
        extra_params: dict[str, Any],
        active: bool,
        request_bot_id: UUID,
    ) -> dict[str, object]:
        if request_bot_id != record_bot_id:
            raise BindingBotIdMismatchError("bot_id mismatch")

        binding = BindingRow(
            id=uuid4(),
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            purpose=purpose,
            model_id=model_id,
            rank=rank,
            variant=variant,
            weight=weight,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            extra_params=dict(extra_params),
            active=active,
            version=1,
        )
        created = await self._repo.create_binding(binding)

        async with self._uow_factory() as uow:
            # Admin-side bindings change events ride the same outbox flow
            # as bot CRUD. The cross-replica listener invalidates by
            # ``record_bot_id`` so the slug is informational; deriving
            # from the tenant UUID via the resolver matches the migration
            # backfill rule and keeps the NOT NULL CHECK satisfied without
            # forcing every admin route to thread the slug.
            from ragbot.shared.workspace_id_validator import resolve_workspace_id
            ws_slug = resolve_workspace_id(
                None, record_tenant_id=record_tenant_id,
            )
            await uow.add_outbox(
                BotConfigUpdated(
                    occurred_at=datetime.datetime.now(tz=datetime.timezone.utc),
                    record_tenant_id=record_tenant_id,
                    trace_id=TraceId(trace_id),
                    workspace_id=ws_slug,
                    record_bot_id=record_bot_id,
                    bot_version_old=0,
                    bot_version_new=0,
                    fields_changed=["bindings"],
                ),
            )
            await uow.commit()

        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                actor_user_id=actor_user_id,
                action="create",
                resource_type="binding",
                resource_id=created.id,
                before=None,
                after={"purpose": purpose, "model_id": str(model_id), "rank": rank},
                reason=None,
                trace_id=trace_id,
            ),
        )

        await self._safe_invalidate(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id)
        return _binding_dict(created)

    async def update_binding(
        self,
        *,
        binding_id: UUID,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        actor_user_id: str,
        trace_id: str,
        fields: dict[str, Any],
    ) -> dict[str, object]:
        before = await self._repo.get_binding(binding_id, record_tenant_id=record_tenant_id)
        if before is None:
            raise BindingNotFoundError("binding not found")
        try:
            updated = await self._repo.update_binding(
                binding_id, record_tenant_id=record_tenant_id, **fields,
            )
        except RepositoryError as exc:
            # Atomic UPDATE returned rowcount=0 — TOCTOU lost the race; map
            # to the public 404 surface (Issue #20).
            raise BindingNotFoundError("binding not found") from exc
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                actor_user_id=actor_user_id,
                action="update",
                resource_type="binding",
                resource_id=binding_id,
                before=_binding_dict(before),
                after=_binding_dict(updated),
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id)
        return _binding_dict(updated)

    async def delete_binding(
        self,
        *,
        binding_id: UUID,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        actor_user_id: str,
        trace_id: str,
    ) -> None:
        before = await self._repo.get_binding(binding_id, record_tenant_id=record_tenant_id)
        if before is None:
            raise BindingNotFoundError("binding not found")
        try:
            await self._repo.delete_binding(binding_id, record_tenant_id=record_tenant_id)
        except RepositoryError as exc:
            # Atomic UPDATE returned rowcount=0 — TOCTOU lost the race; map
            # to the public 404 surface (Issue #20).
            raise BindingNotFoundError("binding not found") from exc
        await self._repo.write_audit(
            AuditEntry(
                record_tenant_id=record_tenant_id,
                record_bot_id=record_bot_id,
                actor_user_id=actor_user_id,
                action="delete",
                resource_type="binding",
                resource_id=binding_id,
                before=_binding_dict(before),
                after=None,
                reason=None,
                trace_id=trace_id,
            ),
        )
        await self._safe_invalidate(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id)

    # ── Audit ──────────────────────────────────────────────────────────────

    async def list_audit(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        limit: int = DEFAULT_AUDIT_LIST_LIMIT,
    ) -> list[Any]:
        rows = await self._repo.list_audit(
            record_tenant_id=record_tenant_id, record_bot_id=record_bot_id, limit=limit,
        )
        return list(rows)

    # ── Cache ──────────────────────────────────────────────────────────────

    async def cache_reload(self) -> int:
        try:
            return await self._resolver.bootstrap_cache()
        except AttributeError:  # pragma: no cover
            return 0

    async def cache_status(self) -> dict[str, Any]:
        try:
            return await self._resolver.cache_status()
        except AttributeError:  # pragma: no cover
            return {"entries": 0, "note": "cache_status not implemented"}

    # ── Effective config ───────────────────────────────────────────────────

    async def effective_config(
        self, *, model_id: UUID, bot_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        try:
            cfg = await self._resolver.preview_runtime(
                model_id=model_id, bot_id=bot_id,
            )
        except AttributeError:
            cfg = None
        return cfg.mask() if cfg else None

    # ── Private helpers ────────────────────────────────────────────────────

    async def _safe_invalidate_all(self) -> None:
        try:
            await self._resolver.invalidate_all()
        except AttributeError:  # pragma: no cover
            pass

    async def _safe_invalidate(
        self, *, record_tenant_id: TenantId, record_bot_id: BotId,
    ) -> None:
        try:
            await self._resolver.invalidate(record_tenant_id=record_tenant_id, record_bot_id=record_bot_id)
        except AttributeError:  # pragma: no cover
            pass


__all__ = [
    "AIConfigService",
    "BindingBotIdMismatchError",
    "BindingNotFoundError",
    "KeyNotFoundError",
    "KeyVerifyFailedError",
    "ModelDeleteConflictError",
    "ModelNotFoundError",
    "ProviderNotFoundError",
]
