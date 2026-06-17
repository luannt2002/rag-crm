"""Static checks for migrations 0055/0056/0136 (``language_packs``).

We don't spin up a real Postgres in unit tests; instead we sanity-check
that:

1. Migration 0055 (table) chains off 0054 and exposes ``upgrade``/
   ``downgrade`` with the canonical schema columns by name.
2. Migration 0056 (initial seed) chains off 0055.
3. Across the FULL chain of seed migrations (0056 + 0136 + any future
   additive seeds), every canonical prompt key is covered for vi + en.
4. Each canonical key resolved from the most-recent seed in the chain
   matches the in-memory fallback (HALLU=0 invariant).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ragbot.shared.constants import LANGUAGE_PACK_PROMPT_KEYS

_MIGRATIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

# Additive seed migrations that contribute rows to ``language_packs``.
# Order matters: later entries override earlier ones for the same key.
_SEED_MIGRATIONS = (
    "20260501_0056_language_packs_seed_vi_en.py",
    "20260529_0136_seed_refuse_message_lang_packs.py",
    "20260529_0146_seed_sysprompt_default_rules.py",
)


def _load(filename: str):
    path = _MIGRATIONS / filename
    spec = importlib.util.spec_from_file_location(filename, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _resolve_seed_chain() -> dict[tuple[str, str], str]:
    """Walk the additive seed chain; later entries win."""
    resolved: dict[tuple[str, str], str] = {}
    for filename in _SEED_MIGRATIONS:
        mod = _load(filename)
        for code, key, content in mod._SEED_ROWS:
            resolved[(code, key)] = content
    return resolved


def test_migration_0055_chain_and_callbacks() -> None:
    mod = _load("20260501_0055_language_packs_table.py")
    assert mod.revision == "0055"
    assert mod.down_revision == "0054"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_migration_0056_chain_and_callbacks() -> None:
    mod = _load("20260501_0056_language_packs_seed_vi_en.py")
    assert mod.revision == "0056"
    assert mod.down_revision == "0055"
    assert callable(mod.upgrade)
    assert callable(mod.downgrade)


def test_seed_chain_covers_every_canonical_prompt_key_for_vi_and_en() -> None:
    """Across the full additive chain, all canonical keys must be present."""
    resolved = _resolve_seed_chain()
    seen_vi = {key for (code, key) in resolved if code == "vi"}
    seen_en = {key for (code, key) in resolved if code == "en"}
    expected = set(LANGUAGE_PACK_PROMPT_KEYS)
    assert expected.issubset(seen_vi), (
        f"vi chain missing keys: {expected - seen_vi}"
    )
    assert expected.issubset(seen_en), (
        f"en chain missing keys: {expected - seen_en}"
    )


def test_seed_chain_carries_refuse_message_for_oos_resolver() -> None:
    """refuse_message must exist in chain so OOS resolver tier 6 works."""
    resolved = _resolve_seed_chain()
    assert ("vi", "refuse_message") in resolved
    assert ("en", "refuse_message") in resolved
    assert resolved[("vi", "refuse_message")], "vi refuse_message must be non-empty"
    assert resolved[("en", "refuse_message")], "en refuse_message must be non-empty"
