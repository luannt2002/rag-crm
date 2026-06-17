"""Unit tests for AIConfigService.add_key / verify_key / list_keys (Phase 3).

All DB and HTTP calls are mocked — no live network or DB required.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragbot.application.services.ai_config_service import (
    AIConfigService,
    KeyNotFoundError,
    KeyVerifyFailedError,
    ProviderNotFoundError,
)
from ragbot.application.ports.ai_config_port import ProviderRow


# ── Fixtures ────────────────────────────────────────────────────────────────

PROVIDER_ID = uuid.uuid4()
KEY_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()
ACTOR = "admin@test"
TRACE = "trace-unit-001"
PLAIN_KEY = "jina_abcdefghijklmnopqrstuvwxyz1234"  # 34-char fake key


def _make_provider(code: str = "jina_ai") -> ProviderRow:
    return ProviderRow(
        id=PROVIDER_ID,
        name="Jina Test",
        code=code,
        type="reranker",
        base_url="https://api.jina.ai/v1",
        auth_type="bearer",
        credentials_vault_path=None,
        enabled=True,
        metadata={},
    )


def _make_service(repo: object) -> AIConfigService:
    svc = AIConfigService(
        ai_config_repo=repo,
        model_resolver=MagicMock(),
        uow_factory=MagicMock(),
        session_factory=MagicMock(),
    )
    # Silence invalidate_all
    svc._safe_invalidate_all = AsyncMock()
    return svc


def _mock_repo(
    provider: ProviderRow | None = None,
    key_row: dict | None = None,
    list_keys_result: list | None = None,
    insert_key_returns: uuid.UUID | None = None,
) -> MagicMock:
    repo = MagicMock()
    repo.get_provider = AsyncMock(return_value=provider or _make_provider())
    repo.get_key = AsyncMock(return_value=key_row)
    repo.list_keys = AsyncMock(return_value=list_keys_result or [])
    repo.insert_key = AsyncMock(return_value=insert_key_returns or KEY_ID)
    repo.mark_key_rotated_out = AsyncMock()
    repo.update_key_health = AsyncMock()
    repo.write_audit = AsyncMock()
    return repo


# ── Tests ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_key_verify_pass_inserts_row() -> None:
    """add_key with verify_first=True and Jina returning 200 should insert and return key_id."""
    repo = _mock_repo()
    svc = _make_service(repo)

    with (
        patch(
            "ragbot.application.services.ai_config_service._verify_provider_key",
            new=AsyncMock(return_value=(True, 200, 42.5, "ok")),
        ),
        patch(
            "ragbot.infrastructure.security.env_secrets.EnvSecretsAdapter.encrypt",
            return_value="encrypted_blob",
        ),
    ):
        result = await svc.add_key(
            provider_id=PROVIDER_ID,
            plain_key=PLAIN_KEY,
            set_as_default=False,
            verify_first=True,
            record_tenant_id=TENANT_ID,
            actor_user_id=ACTOR,
            trace_id=TRACE,
        )

    assert result["ok"] is True
    assert result["key_id"] == str(KEY_ID)
    assert "..." in result["fingerprint"]
    assert result["verified_latency_ms"] == pytest.approx(42.5)
    repo.insert_key.assert_awaited_once()
    repo.write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_key_verify_fail_rejects() -> None:
    """add_key with verify_first=True and Jina returning 403 must raise KeyVerifyFailedError."""
    repo = _mock_repo()
    svc = _make_service(repo)

    with patch(
        "ragbot.application.services.ai_config_service._verify_provider_key",
        new=AsyncMock(return_value=(False, 403, 55.0, "Unauthorized")),
    ):
        with pytest.raises(KeyVerifyFailedError) as exc_info:
            await svc.add_key(
                provider_id=PROVIDER_ID,
                plain_key=PLAIN_KEY,
                set_as_default=False,
                verify_first=True,
                record_tenant_id=TENANT_ID,
                actor_user_id=ACTOR,
                trace_id=TRACE,
            )

    assert exc_info.value.status_code == 403
    repo.insert_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_key_set_as_default_demotes_old() -> None:
    """add_key with set_as_default=True must demote the old default key."""
    old_key_id = uuid.uuid4()
    existing_keys = [
        {"id": old_key_id, "is_default": True, "status": "active"},
    ]
    repo = _mock_repo(list_keys_result=existing_keys)
    svc = _make_service(repo)

    with (
        patch(
            "ragbot.application.services.ai_config_service._verify_provider_key",
            new=AsyncMock(return_value=(True, 200, 30.0, "ok")),
        ),
        patch(
            "ragbot.infrastructure.security.env_secrets.EnvSecretsAdapter.encrypt",
            return_value="encrypted_blob",
        ),
    ):
        result = await svc.add_key(
            provider_id=PROVIDER_ID,
            plain_key=PLAIN_KEY,
            set_as_default=True,
            verify_first=True,
            record_tenant_id=TENANT_ID,
            actor_user_id=ACTOR,
            trace_id=TRACE,
        )

    assert result["ok"] is True
    repo.mark_key_rotated_out.assert_awaited_once_with(old_key_id)
    # New row should be inserted with is_default=True
    call_kwargs = repo.insert_key.call_args.kwargs
    assert call_kwargs["is_default"] is True


@pytest.mark.asyncio
async def test_list_keys_returns_masked_no_plain() -> None:
    """list_keys must return rows that do NOT contain plain_key or api_key_encrypted."""
    masked_rows = [
        {
            "id": KEY_ID,
            "record_provider_id": PROVIDER_ID,
            "fingerprint": "jina_abc...1234",
            "status": "active",
            "is_default": True,
            "last_health_check_at": None,
            "last_health_status": None,
            "last_used_at": None,
            "rotated_at": None,
            "created_at": None,
        }
    ]
    repo = _mock_repo(list_keys_result=masked_rows)
    svc = _make_service(repo)

    rows = await svc.list_keys(provider_id=PROVIDER_ID)

    assert len(rows) == 1
    row = rows[0]
    assert "plain_key" not in row
    assert "api_key_encrypted" not in row
    assert row["fingerprint"] == "jina_abc...1234"
    assert row["is_default"] is True


@pytest.mark.asyncio
async def test_verify_key_updates_health_status() -> None:
    """verify_key must decrypt, probe, then call update_key_health with correct status."""
    encrypted_blob = "some_encrypted_blob"
    key_row = {
        "id": KEY_ID,
        "api_key_encrypted": encrypted_blob,
        "fingerprint": "jina_abc...1234",
        "status": "active",
        "is_default": True,
    }
    repo = _mock_repo(key_row=key_row)
    svc = _make_service(repo)

    with (
        patch(
            "ragbot.infrastructure.security.env_secrets.EnvSecretsAdapter.resolve",
            new=AsyncMock(return_value=PLAIN_KEY),
        ),
        patch(
            "ragbot.application.services.ai_config_service._verify_provider_key",
            new=AsyncMock(return_value=(True, 200, 88.0, "ok")),
        ),
    ):
        result = await svc.verify_key(
            provider_id=PROVIDER_ID,
            key_id=KEY_ID,
            record_tenant_id=TENANT_ID,
            actor_user_id=ACTOR,
            trace_id=TRACE,
        )

    assert result["ok"] is True
    assert result["status_code"] == 200
    assert result["balance_status"] == "ok"
    repo.update_key_health.assert_awaited_once_with(
        KEY_ID, status_code=200, latency_ms=pytest.approx(88.0),
    )
    repo.write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_key_not_found_raises() -> None:
    """verify_key must raise KeyNotFoundError when key_id is absent from DB."""
    repo = _mock_repo(key_row=None)
    svc = _make_service(repo)

    with pytest.raises(KeyNotFoundError):
        await svc.verify_key(
            provider_id=PROVIDER_ID,
            key_id=uuid.uuid4(),
            record_tenant_id=TENANT_ID,
            actor_user_id=ACTOR,
            trace_id=TRACE,
        )


@pytest.mark.asyncio
async def test_add_key_no_verify_skips_probe() -> None:
    """add_key with verify_first=False must NOT call _verify_provider_key."""
    repo = _mock_repo()
    svc = _make_service(repo)

    with (
        patch(
            "ragbot.application.services.ai_config_service._verify_provider_key",
        ) as mock_verify,
        patch(
            "ragbot.infrastructure.security.env_secrets.EnvSecretsAdapter.encrypt",
            return_value="encrypted_blob",
        ),
    ):
        result = await svc.add_key(
            provider_id=PROVIDER_ID,
            plain_key=PLAIN_KEY,
            set_as_default=False,
            verify_first=False,
            record_tenant_id=TENANT_ID,
            actor_user_id=ACTOR,
            trace_id=TRACE,
        )

    mock_verify.assert_not_called()
    assert result["ok"] is True
    assert result["verified_latency_ms"] is None
