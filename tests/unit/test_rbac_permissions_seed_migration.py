"""Pin — the RBAC ``module_permissions`` seed migration provisions a fresh DB.

Bug #7: every ``module:permission`` gate was seeded by run-once scripts, not
alembic, so ``alembic upgrade head`` alone left ``module_permissions`` empty and
the fail-closed RBAC check 403'd every gated route. Migration
``seed_module_permissions_rbac_260710`` folds the complete set into alembic.

This test pins the migration's seed contents so a fresh DB matches production:
    1. The seed has no duplicate (module, permission) pairs.
    2. The production streaming gate ``chat:stream`` is present (the route that
       was 403ing on a fresh DB — see also the workspace_id 500 fix).
    3. The other route-critical gates are present at their live levels.
    4. Levels are sourced from the SSoT (no hardcoded tier integers leaked in).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from ragbot.shared.constants import DEFAULT_SERVICE_LEVEL
from ragbot.shared.rbac import ROLE_LEVELS

_MIGRATION = pathlib.Path(
    "alembic/versions/20260710_seed_module_permissions_rbac.py"
)


def _load_seed() -> list[tuple[str, str, int]]:
    spec = importlib.util.spec_from_file_location("_rbac_seed_mig", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MODULE_PERMISSION_SEED


def test_seed_has_no_duplicate_pairs():
    seed = _load_seed()
    pairs = [(m, p) for m, p, _ in seed]
    assert len(pairs) == len(set(pairs)), "duplicate (module, permission) in seed"


def test_chat_stream_gate_present():
    """The production ``POST /chat/stream`` gate must be seeded — this is the
    exact permission that fail-closed 403'd on a fresh (alembic-only) DB."""
    seed = {(m, p): lvl for m, p, lvl in _load_seed()}
    assert ("chat", "stream") in seed, "chat:stream missing — /chat/stream 403s"
    assert seed[("chat", "stream")] == DEFAULT_SERVICE_LEVEL


@pytest.mark.parametrize(
    ("module", "permission", "expected_level"),
    [
        ("chat", "submit", ROLE_LEVELS["viewer"]),
        ("chat", "feedback", ROLE_LEVELS["viewer"]),
        ("document", "ingest", ROLE_LEVELS["operator"]),
        ("sync", "documents_upsert", ROLE_LEVELS["admin"]),
        ("tenant", "policy_update", ROLE_LEVELS["super_admin"]),
    ],
)
def test_route_critical_gates_present(module, permission, expected_level):
    seed = {(m, p): lvl for m, p, lvl in _load_seed()}
    assert (module, permission) in seed, f"{module}:{permission} missing from seed"
    assert seed[(module, permission)] == expected_level


def test_all_levels_are_known_role_levels():
    """Every seeded level is a real tier (SSoT), never a stray hardcoded number."""
    valid = set(ROLE_LEVELS.values()) | {DEFAULT_SERVICE_LEVEL}
    for module, permission, lvl in _load_seed():
        assert lvl in valid, f"{module}:{permission} level {lvl} not a known tier"
