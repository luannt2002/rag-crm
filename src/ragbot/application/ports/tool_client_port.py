"""Tool Client Protocol — Strategy Pattern for MCP / tool-use clients.

Strategy port for swap-able tool clients (MCP, OpenAI tool-use,
Anthropic tool-use, future SAP / proprietary RPC).

Default implementation is :class:`NullToolClient` (tools off). Heavy
adapters are opt-in via ``system_config.tool_client_provider``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolClientPort(Protocol):
    """Strategy interface for tool-use clients."""

    async def list_tools(self) -> list[dict]:
        """Return the catalogue of tools the client can invoke."""
        ...

    async def call(self, tool_name: str, args: dict) -> dict:
        """Invoke a tool by name with the provided arguments."""
        ...

    def get_provider_name(self) -> str:
        ...


__all__ = ["ToolClientPort"]
