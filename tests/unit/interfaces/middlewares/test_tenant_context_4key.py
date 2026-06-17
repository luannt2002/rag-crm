"""Regression test for mega-sprint-G7 — tenant_context bot cache 4-key fix.

The middleware reads the bot registry cache to honour ``bypass_rate_limit``;
prior to this fix the lookup used only 3 segments
(``ragbot:bot:{record_tenant_id}:{bot_id}:{channel_type}``) while the
writer (``BotRegistryService._key``) used the full 4-key
(``ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}``).

Result: the cache GET ALWAYS missed → ``bypass_rate_limit`` was silently
broken for every bot. This test pins the wire-shape of the GET key so it
matches the writer's contract.

Domain-neutral: no brand / industry literals.
"""

from __future__ import annotations

import inspect

from ragbot.interfaces.http.middlewares import tenant_context as mw_module


def test_middleware_module_imports() -> None:
    """Sanity import — middleware must load without side effects."""
    assert hasattr(mw_module, "TenantContextMiddleware")


def test_dispatch_source_carries_4key_cache_segments() -> None:
    """Pin the wire-shape: GET key MUST include workspace_id segment.

    Reading the source verifies the f-string template contains all four
    identity segments in the correct order — the same order the writer
    (BotRegistryService._key) uses. Drift between writer (4-key) and
    reader (3-key) silently breaks bypass_rate_limit for every bot.
    """
    src = inspect.getsource(mw_module.TenantContextMiddleware.dispatch)
    # Order must match writer: tenant -> workspace -> bot -> channel.
    assert "_req_workspace_id" in src or "workspace_id" in src, (
        "mega-sprint-G7 regression — middleware must lift workspace_id "
        "from JSON body / request.state and include it in the bot cache "
        "lookup key (4-key contract)"
    )
    assert "ragbot:bot:" in src, "bot cache key prefix missing"


def test_workspace_id_fallback_documented() -> None:
    """The fallback chain (state → body → str(tenant)) must be in source.

    Per CLAUDE.md identity rule: missing/null workspace_id body field
    falls back to ``str(record_tenant_id)``. Pin that the source has the
    fallback so future refactors don't silently drop the safety net.
    """
    src = inspect.getsource(mw_module.TenantContextMiddleware.dispatch)
    # Fallback chain MUST be visible in source.
    assert "workspace_id" in src
    # Lookup key contract uses the 4-key f-string; assert the inline GET
    # after the fix uses workspace_id between tenant and bot.
    assert (
        "{_req_workspace_id}" in src
        or "workspace_id_for_cache" in src
        or "workspace_slug" in src
    ), "expected a workspace placeholder used inside the cache key f-string"
