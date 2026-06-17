"""Multi-query expansion default ON contract.

Pins the platform-level default and ensures the seed file + chat_worker
forwarding stay aligned, so the multi-query node fires unless a bot
explicitly opts out via ``plan_limits`` or ``system_config``.
"""

from __future__ import annotations


def test_multi_query_default_on() -> None:
    """Constant must be ``True`` so the default-deploy path uses multi-query."""
    from ragbot.shared.constants import DEFAULT_MULTI_QUERY_ENABLED

    assert DEFAULT_MULTI_QUERY_ENABLED is True


def test_multi_query_model_default_is_string() -> None:
    """Model default must be a non-empty string so router can resolve it."""
    from ragbot.shared.constants import DEFAULT_MULTI_QUERY_MODEL

    assert isinstance(DEFAULT_MULTI_QUERY_MODEL, str)
    # DEPRECATED 2026-05-14 AdapChunk-reorg: explicit "haiku" replaces "auto"
    # to prevent resolve_runtime spike to Sonnet/Opus on cost path. Phần 21.3 W5.
    # assert DEFAULT_MULTI_QUERY_MODEL == "auto"
    assert DEFAULT_MULTI_QUERY_MODEL == "haiku"


def test_multi_query_n_variants_within_max() -> None:
    """N variants must respect the safety cap to bound retrieval fan-out."""
    from ragbot.shared.constants import (
        DEFAULT_MULTI_QUERY_MAX_VARIANTS,
        DEFAULT_MULTI_QUERY_N_VARIANTS,
    )

    assert DEFAULT_MULTI_QUERY_N_VARIANTS <= DEFAULT_MULTI_QUERY_MAX_VARIANTS
    assert DEFAULT_MULTI_QUERY_N_VARIANTS >= 1


def test_init_system_config_seed_matches_constant() -> None:
    """Seed value must match the constant (``true``)."""
    from pathlib import Path

    seed_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "init_system_config.py"
    )
    body = seed_path.read_text(encoding="utf-8")

    assert '("multi_query_enabled", "true"' in body, (
        "Seed for multi_query_enabled drifted from DEFAULT_MULTI_QUERY_ENABLED."
    )


def test_chat_worker_wires_all_multi_query_keys() -> None:
    """chat_worker pipeline_config must forward every multi_query_* key.

    Any key referenced in ``query_graph._pcfg`` must appear in chat_worker
    so per-bot DB / plan_limits overrides reach the rerank node.
    """
    from pathlib import Path

    # chat_worker was split into a package — scan every module.
    pkg = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "ragbot"
        / "interfaces"
        / "workers"
        / "chat_worker"
    )
    body = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(pkg.glob("*.py"))
    )

    for key in (
        '"multi_query_enabled"',
        '"multi_query_n_variants"',
        '"multi_query_max_variants"',
        '"multi_query_timeout_s"',
        '"multi_query_model"',
    ):
        assert key in body, (
            f"chat_worker pipeline_config missing forwarded key {key}; "
            "_pcfg fall-through to constant default = silent override loss"
        )
