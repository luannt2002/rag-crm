"""P2 pin — chunking-strategy constant usage + compliant broad-excepts.

Two invariants this branch must not regress:

1. (b) The 5 chunking-strategy identity names are NAMED CONSTANTS in
   ``ragbot.shared.constants`` (not inline string literals in the
   strategy-selection logic). Values must stay byte-identical to the
   dispatch strings AND stay consistent across the three independent
   sources that encode the strategy-name set (constants,
   ``DEFAULT_STRATEGY_WEIGHTS`` keys, resolver ``_ALLOWED``).

2. (a) The two ``except Exception`` sites in the chunking/ingest input-data
   code keep an explicit ``# noqa: BLE001`` + reason AND keep their
   degrade-silent behaviour (langdetect failure -> 0.0 confidence;
   observability stats raising -> never propagates, chunking completes).

Pure-function tests — no DB, no network.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
import types
from pathlib import Path

import ragbot.shared.chunk_quality as chunk_quality
import ragbot.shared.chunking.strategies as strategies
from ragbot.shared.chunking.analyze import select_strategy
from ragbot.shared.constants import (
    CHUNK_STRATEGY_HDT,
    CHUNK_STRATEGY_HYBRID,
    CHUNK_STRATEGY_PROPOSITION,
    CHUNK_STRATEGY_RECURSIVE,
    CHUNK_STRATEGY_SEMANTIC,
    DEFAULT_STRATEGY_WEIGHTS,
)

_SRC = Path(__file__).resolve().parents[2] / "src" / "ragbot"

_CONSTANT_VALUES = {
    CHUNK_STRATEGY_HDT,
    CHUNK_STRATEGY_SEMANTIC,
    CHUNK_STRATEGY_RECURSIVE,
    CHUNK_STRATEGY_HYBRID,
    CHUNK_STRATEGY_PROPOSITION,
}


def test_strategy_constant_values_are_byte_identical() -> None:
    assert CHUNK_STRATEGY_HDT == "hdt"
    assert CHUNK_STRATEGY_SEMANTIC == "semantic"
    assert CHUNK_STRATEGY_RECURSIVE == "recursive"
    assert CHUNK_STRATEGY_HYBRID == "hybrid"
    assert CHUNK_STRATEGY_PROPOSITION == "proposition"


def test_strategy_constants_match_weights_keys() -> None:
    assert set(DEFAULT_STRATEGY_WEIGHTS.keys()) == _CONSTANT_VALUES


def test_strategy_constants_match_resolver_allowlist() -> None:
    from ragbot.infrastructure.chunking_strategy.llm_resolver import _ALLOWED

    assert set(_ALLOWED) == _CONSTANT_VALUES


def test_select_strategy_returns_named_strategy_only() -> None:
    hdt_profile = {
        "total_headings": 12,
        "total_words": 4000,
        "heading_counts": {"h1": 2, "h2": 6, "h3": 4},
        "table_count": 0,
        "avg_text_length": 40.0,
        "mixed_content_score": 0.0,
        "has_toc": True,
        "is_csv_format": False,
        "vn_hierarchical_markers": 0,
    }
    strat, conf = select_strategy(hdt_profile)
    assert strat == CHUNK_STRATEGY_HDT
    assert strat in _CONSTANT_VALUES
    assert 0.0 <= conf <= 1.0

    bland_profile = {
        "total_headings": 0,
        "total_words": 5,
        "heading_counts": {"h1": 0, "h2": 0, "h3": 0},
        "table_count": 0,
        "avg_text_length": 1.0,
        "mixed_content_score": 0.0,
        "has_toc": False,
        "is_csv_format": False,
        "vn_hierarchical_markers": 0,
    }
    strat2, conf2 = select_strategy(bland_profile)
    assert strat2 in _CONSTANT_VALUES
    assert 0.0 <= conf2 <= 1.0


def test_no_inline_strategy_literals_in_analyze_logic() -> None:
    text = (_SRC / "shared" / "chunking" / "analyze.py").read_text(encoding="utf-8")
    body = text[text.index("def select_strategy("):]
    offending: list[str] = []
    in_doc = False  # skip docstring bodies (rule explanations may quote "hdt")
    for raw in body.splitlines():
        line = raw.strip()
        q = line.count('"""')
        if in_doc:
            if q:
                in_doc = False
            continue
        if q == 1:  # a docstring opens here (and does not close on the same line)
            in_doc = True
            continue
        if line.startswith("#") or line.startswith('"') or line.startswith("*"):
            continue
        for name in ("hdt", "semantic", "recursive", "hybrid", "proposition"):
            if re.search(rf'(==|!=|\[|\()\s*"{name}"', line):
                offending.append(line)
    assert offending == [], f"inline strategy literal in logic: {offending}"


def test_chunk_quality_except_has_noqa_reason() -> None:
    src = inspect.getsource(chunk_quality)
    assert re.search(
        r"except Exception:\s*#\s*noqa:\s*BLE001\b.*langdetect", src
    ), "chunk_quality langdetect except lost its noqa+reason"


def test_strategies_stats_except_has_noqa_reason() -> None:
    src = inspect.getsource(strategies)
    assert re.search(
        r"except Exception:\s*#\s*noqa:\s*BLE001\b.*observability", src
    ), "strategies stats except lost its noqa+reason"


def test_langdetect_failure_degrades_to_zero_confidence(monkeypatch) -> None:
    fake = types.ModuleType("langdetect")

    class _Boom(Exception):
        pass

    def _detect_langs(_chunk):  # noqa: ANN001,ANN202 - test stub
        raise _Boom("simulated langdetect failure")

    fake.DetectorFactory = type("DetectorFactory", (), {"seed": 0})
    fake.detect_langs = _detect_langs
    monkeypatch.setitem(sys.modules, "langdetect", fake)

    conf = chunk_quality._score_language_confidence("some non-empty text here")
    assert conf == 0.0


def test_stats_exception_degrades_and_completes() -> None:
    class _Port:
        provider_name = "test"

        async def similarity(self, _a, _b):  # noqa: ANN001,ANN202
            return 0.0  # force a split at every boundary

        def stats(self):  # noqa: ANN201
            raise RuntimeError("simulated stats failure")

    text = "First sentence here. Second very different sentence. Third one too."
    chunks = asyncio.run(
        strategies._chunk_semantic_embed(text, similarity_port=_Port())
    )
    assert isinstance(chunks, list)
    assert chunks  # chunking completed despite the stats() failure
