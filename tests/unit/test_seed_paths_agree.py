"""Guard: the two system_config bootstrap paths must agree on shared keys.

A fresh DB can be seeded two ways:
  1. ``alembic upgrade head`` runs migration 0020 (bootstrap-of-record).
  2. ``scripts/init_system_config.py`` UPSERTs its own SEED_CONFIGS list.

If a key appears in BOTH lists with different values, a DB seeded by the
script silently diverges from one migrated via alembic (different answer
budget, rerank-set size, ...). Migration 0020 is the source of truth; the
init script must mirror it for every shared key.

This test FAILED before the W5 config-drift sync (llm_default_max_tokens:
1024 vs 450; rag_rerank_top_n: 10 vs 5).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_seed_configs(rel_path: str, mod_name: str) -> dict[str, str]:
    """Load a module by file path and return its SEED_CONFIGS as {key: value}."""
    path = _PROJECT_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {k: v for k, v, *_ in mod.SEED_CONFIGS}


def test_init_script_matches_alembic_0020_for_shared_keys() -> None:
    alembic_seed = _load_seed_configs(
        "alembic/versions/20260417_0020_seed_system_config.py", "_seed_alembic_0020"
    )
    script_seed = _load_seed_configs(
        "scripts/init_system_config.py", "_seed_init_script"
    )

    shared = alembic_seed.keys() & script_seed.keys()
    assert shared, (
        "expected overlapping bootstrap keys between alembic 0020 and init script"
    )

    drift = {
        key: (alembic_seed[key], script_seed[key])
        for key in shared
        if alembic_seed[key] != script_seed[key]
    }
    assert not drift, (
        "system_config seed drift (alembic_0020_value, init_script_value): "
        f"{drift} — migration 0020 is bootstrap-of-record; sync the init script to it."
    )
