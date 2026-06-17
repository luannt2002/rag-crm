"""4-key bot identity invariant pinned at every layer.

The bot resolve contract is the 4-tuple
``(record_tenant_id, workspace_id, bot_id, channel_type)``. Tenant and
workspace are independent isolation boundaries: a single tenant may host
multiple workspaces, and a single workspace MUST NOT collide with another
workspace's ``(bot_id, channel_type)`` slug. This suite locks the contract
shape so a future regression cannot collapse the resolver back to a
narrower 3-key tuple at any of these layers:

1. ``BotConfig`` Pydantic DTO — both ``record_tenant_id`` and
   ``workspace_id`` REQUIRED.
2. ``BotRegistryService.lookup`` — 4 positional args in order.
3. ``SqlAlchemyBotRepository.find_by_4key`` — 4 positional args in order.
4. ``BotRepositoryPort`` Protocol matches the implementation.
5. ``BotModel`` table — ``UniqueConstraint`` covers the full 4-tuple
   under the canonical name; the legacy 3-key name is gone.
6. ``BotModel`` columns — ``record_tenant_id`` + ``workspace_id`` both
   ``NOT NULL``.
7. Redis cache key shape — 4 segments after the prefix, in the canonical
   order.
8. ``create_bot`` repository entry rejects a missing or empty
   ``workspace_id`` slug at the validator boundary, never silently
   inserting a tenant-fallback row.
"""

from __future__ import annotations

import inspect
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from ragbot.application.dto.bot_config import BotConfig
from ragbot.application.ports.repository_ports import BotRepositoryPort
from ragbot.application.services.bot_registry_service import (
    REDIS_PREFIX,
    BotRegistryService,
)
from ragbot.infrastructure.db.models import BotModel
from ragbot.infrastructure.repositories.bot_repository import (
    SqlAlchemyBotRepository,
)
from ragbot.shared.errors import WorkspaceIdInvalid


# ---------------------------------------------------------------------------
# 1. BotConfig DTO requires both tenant + workspace identity slugs
# ---------------------------------------------------------------------------


class TestBotConfigRequiresWorkspaceId:
    def test_workspace_id_field_present(self) -> None:
        assert "workspace_id" in BotConfig.model_fields, (
            "BotConfig must expose workspace_id; the resolver fans the slug "
            "into Redis cache keys + cross-tenant guards"
        )

    def test_workspace_id_missing_rejected(self) -> None:
        rt = uuid4()
        with pytest.raises(ValidationError) as exc_info:
            BotConfig(
                id=uuid4(),
                bot_id="support",
                channel_type="web",
                record_tenant_id=rt,
                # workspace_id deliberately omitted
                bot_name="bot",
            )
        missing = [e for e in exc_info.value.errors() if e.get("type") == "missing"]
        assert any(e["loc"] == ("workspace_id",) for e in missing), (
            f"missing workspace_id must surface as a field-level error; "
            f"got: {exc_info.value.errors()!r}"
        )

    def test_workspace_id_invalid_format_rejected(self) -> None:
        # ``BotConfig.workspace_id_valid`` defers to ``WorkspaceIdValidator``
        # which raises ``WorkspaceIdInvalid`` directly. Pydantic surfaces it
        # without wrapping when the validator raises a non-``ValueError``
        # exception, so accept either path here — both block the ingest.
        with pytest.raises((ValidationError, WorkspaceIdInvalid)):
            BotConfig(
                id=uuid4(),
                bot_id="support",
                channel_type="web",
                record_tenant_id=uuid4(),
                workspace_id="has space",
                bot_name="bot",
            )

    def test_workspace_id_accepts_valid_slug(self) -> None:
        rt = uuid4()
        cfg = BotConfig(
            id=uuid4(),
            bot_id="support",
            channel_type="web",
            record_tenant_id=rt,
            workspace_id="sales-q4",
            bot_name="bot",
        )
        assert cfg.workspace_id == "sales-q4"


# ---------------------------------------------------------------------------
# 2. Resolver signatures — repo + registry both 4-positional
# ---------------------------------------------------------------------------


class TestResolverSignaturesAre4Key:
    """The lookup chain must accept all 4 keys positionally and in order.

    A drift to keyword-only or to a 3-arg form would silently break callers
    that pass them positionally; pin both the order and the kind.
    """

    def test_repository_find_by_4key_signature(self) -> None:
        sig = inspect.signature(SqlAlchemyBotRepository.find_by_4key)
        params = list(sig.parameters.values())
        # params[0] is ``self``; the 4 keys follow in canonical order.
        assert [p.name for p in params[1:5]] == [
            "record_tenant_id",
            "workspace_id",
            "bot_id",
            "channel_type",
        ]
        # All 4 are positional-or-keyword (POSITIONAL_OR_KEYWORD),
        # never keyword-only — callers commonly pass them positionally.
        for p in params[1:5]:
            assert p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD, (
                f"find_by_4key.{p.name} must accept positional args "
                f"(kind={p.kind!r})"
            )
            assert p.default is inspect.Parameter.empty, (
                f"find_by_4key.{p.name} must NOT have a default — every "
                f"call site has to pass the 4-key explicitly"
            )

    def test_registry_lookup_signature(self) -> None:
        sig = inspect.signature(BotRegistryService.lookup)
        params = list(sig.parameters.values())
        assert [p.name for p in params[1:5]] == [
            "record_tenant_id",
            "workspace_id",
            "bot_id",
            "channel_type",
        ]
        for p in params[1:5]:
            assert p.default is inspect.Parameter.empty, (
                f"BotRegistryService.lookup.{p.name} must NOT have a default"
            )

    def test_port_matches_impl(self) -> None:
        port_sig = inspect.signature(BotRepositoryPort.find_by_4key)
        impl_sig = inspect.signature(SqlAlchemyBotRepository.find_by_4key)
        assert (
            list(port_sig.parameters.keys())[1:]
            == list(impl_sig.parameters.keys())[1:5]
        ), "Port and implementation must agree on the 4-key parameter order"


# ---------------------------------------------------------------------------
# 3. ORM constraint — 4-key UNIQUE under the canonical name
# ---------------------------------------------------------------------------


class TestBotModelConstraints:
    def test_unique_constraint_named_4key(self) -> None:
        constraints = {c.name for c in BotModel.__table__.constraints}
        assert "uq_bots_record_tenant_workspace_bot_channel" in constraints, (
            "expected the 4-tuple unique constraint to remain in place"
        )

    def test_backcompat_3key_constraint_dropped(self) -> None:
        """The narrower 3-key constraint would re-open cross-workspace
        slug collisions if it ever came back; assert its absence so a
        rollback that re-adds it surfaces here.
        """
        constraints = {c.name for c in BotModel.__table__.constraints}
        assert "uq_bots_record_tenant_bot_channel" not in constraints

    def test_unique_constraint_columns_in_canonical_order(self) -> None:
        target = next(
            (
                c for c in BotModel.__table__.constraints
                if c.name == "uq_bots_record_tenant_workspace_bot_channel"
            ),
            None,
        )
        assert target is not None
        cols = [c.name for c in target.columns]
        assert cols == [
            "record_tenant_id", "workspace_id", "bot_id", "channel_type",
        ], (
            f"4-key columns must be in canonical resolver order; got {cols!r}"
        )

    def test_workspace_id_column_not_null(self) -> None:
        col = BotModel.__table__.columns["workspace_id"]
        assert col.nullable is False, (
            "workspace_id must be NOT NULL — the resolver substitutes "
            "str(record_tenant_id) before INSERT, so empty rows are a bug"
        )


# ---------------------------------------------------------------------------
# 4. Cache key shape — 4 segments in canonical order
# ---------------------------------------------------------------------------


class TestCacheKeyShape:
    def test_cache_key_contains_4_segments_after_prefix(self) -> None:
        rt = UUID("11111111-2222-3333-4444-555555555555")
        key = BotRegistryService._key(rt, "sales", "support", "web")
        assert key.startswith(f"{REDIS_PREFIX}:")
        # Strip the ``ragbot:bot:`` prefix and split — 4 parts left.
        suffix = key[len(REDIS_PREFIX) + 1:]
        parts = suffix.split(":")
        assert len(parts) == 4, (
            f"cache key suffix must hold 4 segments "
            f"(record_tenant_id:workspace_id:bot_id:channel_type); "
            f"got {parts!r}"
        )
        assert parts == [str(rt), "sales", "support", "web"]

    def test_cache_key_distinct_per_workspace(self) -> None:
        """Two workspaces under the same tenant + same external slug must
        produce distinct keys, otherwise a Redis hit would return the
        wrong bot config across the workspace boundary.
        """
        rt = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        key_sales = BotRegistryService._key(rt, "sales", "support", "web")
        key_marketing = BotRegistryService._key(
            rt, "marketing", "support", "web",
        )
        assert key_sales != key_marketing


# ---------------------------------------------------------------------------
# 5. ``create_bot`` rejects missing slug — validator boundary
# ---------------------------------------------------------------------------


class TestCreateBotRejectsMissingWorkspace:
    """``WorkspaceIdValidator`` is the gatekeeper; calling it directly
    surfaces the empty / None case the way the repository entry path does.

    The repository itself takes ``WorkspaceId`` (a ``NewType[str]``) and
    relies on the caller having already validated. We pin the validator
    behaviour here so callers never bypass it with raw strings.
    """

    def test_validator_rejects_none(self) -> None:
        from ragbot.shared.workspace_id_validator import WorkspaceIdValidator
        with pytest.raises(WorkspaceIdInvalid):
            WorkspaceIdValidator.validate(None)

    def test_validator_rejects_empty(self) -> None:
        from ragbot.shared.workspace_id_validator import WorkspaceIdValidator
        with pytest.raises(WorkspaceIdInvalid):
            WorkspaceIdValidator.validate("")

    def test_create_bot_workspace_id_param_required(self) -> None:
        sig = inspect.signature(SqlAlchemyBotRepository.create_bot)
        param = sig.parameters["workspace_id"]
        assert param.default is inspect.Parameter.empty, (
            "create_bot.workspace_id must have NO default — the caller "
            "resolves the slug via resolve_workspace_id() before INSERT, "
            "and a default would silently mask a missing claim"
        )
        # Keyword-only kind so positional drift can't slip a UUID into
        # the workspace slot.
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"create_bot.workspace_id must be keyword-only; got {param.kind!r}"
        )
