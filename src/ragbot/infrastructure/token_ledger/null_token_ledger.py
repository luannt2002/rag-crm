"""Null Object — token ledger disabled (default-safe, never raises)."""
from __future__ import annotations

from ragbot.application.ports.token_ledger_port import TokenLedgerEntry, TokenLedgerPort


class NullTokenLedger(TokenLedgerPort):
    """No-op sink. Used when ``token_ledger_provider`` is off / unset."""

    def emit(self, entry: TokenLedgerEntry) -> None:  # noqa: ARG002
        return
