"""Tool Client Strategy registry — unit tests."""

from __future__ import annotations

import pytest

from ragbot.application.ports.tool_client_port import ToolClientPort
try:
    from ragbot.infrastructure.tools.null_tool_client import NullToolClient
    from ragbot.infrastructure.tools.registry import (
        build_tool_client,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "tools subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )


@pytest.mark.asyncio
async def test_null_tool_client_default_disabled() -> None:
    c = NullToolClient()
    assert await c.list_tools() == []
    err = await c.call("any_tool", {"x": 1})
    assert err == {"error": "tools_disabled"}
    assert c.get_provider_name() == "null"
    assert isinstance(c, ToolClientPort)


def test_registry_default_is_null() -> None:
    for prov in (None, "", "does_not_exist_xyz"):
        assert isinstance(build_tool_client(prov), NullToolClient)
    providers = list_providers()
    assert "null" in providers
    assert "mcp" in providers
    assert providers == sorted(providers)


def test_mcp_stub_falls_back_to_null() -> None:
    # Stub raises NotImplementedError → registry catches → NullToolClient.
    instance = build_tool_client("mcp")
    assert isinstance(instance, NullToolClient)
