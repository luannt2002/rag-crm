"""Route-introspection helpers for FastAPI lazy-composed routers.

FastAPI (>=0.137) composes ``include_router(...)`` results as lazy
``_IncludedRouter`` wrapper objects instead of copying each leaf
``APIRoute`` into the parent router. Iterating ``router.routes`` therefore
yields wrapper objects whose ``.path`` is ``None`` — the real leaf paths
live one level down, reachable via ``_IncludedRouter.effective_candidates()``
which yields child ``_IncludedRouter`` branches (recurse) or
``_EffectiveRouteContext`` leaves (carry the final composed ``.path`` /
``.methods`` / ``.endpoint``).

These helpers walk that tree so route-registration tests can assert against
the SAME fully-prefixed paths the live ASGI app serves, independent of how
the framework internally stores the composition.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, NamedTuple

from fastapi.routing import (
    APIRoute,
    _EffectiveRouteContext,
    _IncludedRouter,
)


class LeafRoute(NamedTuple):
    """A resolved leaf route with its final composed path."""

    path: str
    methods: frozenset[str]
    endpoint: Callable[..., Any] | None


def iter_leaf_routes(routes: Iterable[Any]) -> list[LeafRoute]:
    """Flatten a router/app ``.routes`` list into resolved leaf routes.

    Recurses through lazy ``_IncludedRouter`` branches and also accepts
    plain ``APIRoute`` objects (sub-routers iterated directly), so the same
    helper works on both ``router.routes`` and a single ``module.router``.
    """
    out: list[LeafRoute] = []
    for r in routes:
        if isinstance(r, _IncludedRouter):
            out.extend(iter_leaf_routes(r.effective_candidates()))
        elif isinstance(r, _EffectiveRouteContext):
            out.append(
                LeafRoute(
                    path=r.path,
                    methods=frozenset(r.methods or ()),
                    endpoint=getattr(r, "endpoint", None),
                )
            )
        elif isinstance(r, APIRoute):
            out.append(
                LeafRoute(
                    path=r.path,
                    methods=frozenset(r.methods or ()),
                    endpoint=getattr(r, "endpoint", None),
                )
            )
    return out


def leaf_paths(routes: Iterable[Any]) -> set[str]:
    """Set of fully-composed leaf paths reachable from ``routes``."""
    return {lr.path for lr in iter_leaf_routes(routes)}


def leaf_method_paths(routes: Iterable[Any]) -> set[tuple[tuple[str, ...], str]]:
    """Set of ``((sorted methods), path)`` tuples for ``routes``."""
    return {
        (tuple(sorted(lr.methods)), lr.path) for lr in iter_leaf_routes(routes)
    }
