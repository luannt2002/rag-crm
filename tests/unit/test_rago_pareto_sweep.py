"""Unit tests — Stream D Phase 4 (rago_pareto_sweep)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from rago_pareto_sweep import (  # noqa: E402
    KnobSpec,
    latin_hypercube_sample,
    parse_schema,
)

SCHEMA_PATH = REPO_ROOT / "docs" / "master" / "16-P-rago-schema.md"


def test_parse_ragschema_yields_at_least_10_knobs() -> None:
    """Schema doc must declare a non-trivial knob set for the sweep to mean
    anything; assert ≥ 10 to catch accidental table truncation."""
    knobs = parse_schema(SCHEMA_PATH)
    assert len(knobs) >= 10, f"only {len(knobs)} knobs parsed from {SCHEMA_PATH}"


def test_parse_ragschema_known_knobs_present() -> None:
    """Wave M3.3-D — knob keys must match PRODUCTION ``system_config`` keys
    (``rag_top_k`` / ``rag_rerank_top_n``) so Pareto sweep flips the SAME
    config rows production reads. Pre-fix the script used legacy keys
    (``top_k_retrieve`` / ``top_k_rerank``) that production stopped reading
    after mega-sprint-G21 rename — Pareto verdicts were on shadow params.
    """
    knobs = {k.key for k in parse_schema(SCHEMA_PATH)}
    expected = {
        "chunk_size",
        "chunk_overlap",
        "rag_top_k",
        "rag_rerank_top_n",
        "multi_query_n_variants",
        "rrf_k",
        "reranker_enabled",
        "grade_use_batch",
    }
    missing = expected - knobs
    assert not missing, f"missing knobs: {missing}"


def test_parse_ragschema_types_coerced_correctly() -> None:
    knobs = {k.key: k for k in parse_schema(SCHEMA_PATH)}
    assert knobs["chunk_size"].knob_type == "int"
    assert isinstance(knobs["chunk_size"].default, int)
    assert knobs["reranker_enabled"].knob_type == "bool"
    assert isinstance(knobs["reranker_enabled"].default, bool)


def test_latin_hypercube_sample_size_matches_n_configs() -> None:
    knobs = parse_schema(SCHEMA_PATH)
    configs = latin_hypercube_sample(knobs, n_configs=30)
    assert len(configs) == 30


def test_latin_hypercube_values_within_sweep_range() -> None:
    knobs = parse_schema(SCHEMA_PATH)
    configs = latin_hypercube_sample(knobs, n_configs=30)
    knob_by_key = {k.key: k for k in knobs}
    for cfg in configs:
        for key, value in cfg.items():
            spec = knob_by_key[key]
            assert value in spec.sweep_values, (
                f"config has {key}={value!r} not in sweep range {spec.sweep_values}"
            )


def test_latin_hypercube_marginal_coverage_uniform() -> None:
    """Each sweep value should appear at least once across N configs (with
    N ≥ bucket count). This guards against a buggy shuffle dropping
    buckets."""
    knobs = parse_schema(SCHEMA_PATH)
    configs = latin_hypercube_sample(knobs, n_configs=30)
    for spec in knobs:
        if len(spec.sweep_values) > 30:
            continue
        seen = {cfg[spec.key] for cfg in configs}
        assert seen >= set(spec.sweep_values), (
            f"{spec.key} sweep values {spec.sweep_values} missing from sample (got {seen})"
        )


def test_latin_hypercube_deterministic_with_seed() -> None:
    knobs = parse_schema(SCHEMA_PATH)
    a = latin_hypercube_sample(knobs, n_configs=10, seed=42)
    b = latin_hypercube_sample(knobs, n_configs=10, seed=42)
    assert a == b, "same seed must produce identical sample"


def test_latin_hypercube_different_seeds_produce_different_samples() -> None:
    knobs = parse_schema(SCHEMA_PATH)
    a = latin_hypercube_sample(knobs, n_configs=10, seed=1)
    b = latin_hypercube_sample(knobs, n_configs=10, seed=2)
    assert a != b, "different seeds should produce different samples"


def test_knob_spec_immutable() -> None:
    spec = KnobSpec(key="chunk_size", knob_type="int", default=1024, sweep_values=(512, 1024))
    with pytest.raises(Exception):  # noqa: B017,PT011 — frozen dataclass raises FrozenInstanceError
        spec.default = 2048  # type: ignore[misc]
