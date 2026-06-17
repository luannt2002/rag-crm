"""Registry — build a TokenLedgerPort from a config provider string."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ragbot.application.ports.token_ledger_port import TokenLedgerPort
from ragbot.infrastructure.token_ledger.async_db_token_ledger import AsyncDBTokenLedger
from ragbot.infrastructure.token_ledger.null_token_ledger import NullTokenLedger

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession


def build_token_ledger(
    provider: str | None,
    *,
    session_factory: "Callable[[], AsyncSession] | None" = None,
) -> TokenLedgerPort:
    """``provider='db'`` → AsyncDBTokenLedger (if session_factory given); else Null."""
    if str(provider or "").strip().lower() == "db" and session_factory is not None:
        return AsyncDBTokenLedger(session_factory)
    return NullTokenLedger()
