"""[T1-Smartness] Track B — locale-scoped routing signals.

Proves the domain-neutral fix: the stats/intent routers read their signal
lists from the per-locale language pack instead of hard-coded Vietnamese
literals, while a ``vi`` bot stays BYTE-IDENTICAL and a non-``vi`` locale
degrades gracefully (routes on its own signals or falls through to vector —
never crashes, never mis-routes).

Covers:
  - vi DEFAULT-seed path == explicit vi signals == legacy behaviour.
  - en signals route English range/list/superlative/price/intent queries.
  - unknown locale resolves to the vi seed (no crash, no regression).
  - an EMPTY-signal locale fires NO route (vector fallback) and never raises.
  - RoutingSignals JSON serde roundtrip is lossless (DB ↔ seed parity).
  - language_pack_from_dict hydrates routing_signals from a JSON row.
  - the alembic seed JSON deserialises back to the in-memory seed (no drift).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ragbot.application.services.heuristic_intent_classifier import (
    classify_heuristic,
)
from ragbot.shared.i18n import (
    RoutingSignals,
    get_routing_signals,
    language_pack_from_dict,
    routing_signals_from_json,
    routing_signals_to_json,
)
from ragbot.shared.query_range_parser import (
    parse_list_query,
    parse_price_of_entity_query,
    parse_range_query,
)


# ---------------------------------------------------------------------------
# vi backward-compat — default path == explicit vi == legacy literals
# ---------------------------------------------------------------------------
class TestViByteIdentical:
    def test_default_none_equals_explicit_vi_range(self) -> None:
        vi = get_routing_signals("vi")
        q = "dưới 2tr có dịch vụ gì"
        assert parse_range_query(q) == parse_range_query(q, signals=vi)

    def test_vi_below_token_routes_max(self) -> None:
        rf = parse_range_query("dưới 500k")
        assert rf is not None
        assert rf.price_max == 500_000
        assert rf.price_min is None

    def test_vi_above_token_routes_min(self) -> None:
        rf = parse_range_query("trên 1 triệu")
        assert rf is not None
        assert rf.price_min == 1_000_000

    def test_vi_superlative_max(self) -> None:
        rf = parse_range_query("dịch vụ đắt nhất")
        assert rf is not None
        assert rf.operation == "max"

    def test_vi_superlative_min(self) -> None:
        rf = parse_range_query("gói rẻ nhất")
        assert rf is not None
        assert rf.operation == "min"

    def test_vi_list_keyword_extraction_unchanged(self) -> None:
        rf = parse_list_query("liệt kê dịch vụ tẩy da chết")
        assert rf is not None
        assert rf.operation == "keyword"
        assert rf.keyword == "tẩy da chết"

    def test_vi_measure_unit_carveout_still_active(self) -> None:
        # "bao nhiêu buổi" is a MEASURE factoid, NOT a catalog count → no
        # keyword route (would otherwise hijack to the name lookup).
        assert parse_list_query("gói dùng tối đa bao nhiêu buổi") is None

    def test_vi_price_of_entity_routes_keyword(self) -> None:
        rf = parse_price_of_entity_query("triệt lông nách giá bao nhiêu")
        assert rf is not None
        assert rf.operation == "keyword"
        assert "triệt lông nách" in rf.keyword

    def test_vi_structural_anchor_blocks_price_route(self) -> None:
        # "Điều 5 ... giá ..." is a legal clause ref, not a catalog price.
        assert parse_price_of_entity_query("Điều 5 quy định giá bao nhiêu") is None

    def test_vi_intent_greeting(self) -> None:
        assert classify_heuristic("xin chào").intent == "greeting"

    def test_vi_intent_aggregation(self) -> None:
        assert classify_heuristic("liệt kê tất cả dịch vụ").intent == "aggregation"

    def test_default_none_equals_explicit_vi_intent(self) -> None:
        vi = get_routing_signals("vi")
        q = "tại sao giá lại khác nhau"
        assert classify_heuristic(q).intent == classify_heuristic(q, signals=vi).intent


# ---------------------------------------------------------------------------
# en — routes on English signals
# ---------------------------------------------------------------------------
class TestEnglishLocaleRoutes:
    def test_en_below_token(self) -> None:
        en = get_routing_signals("en")
        rf = parse_range_query("services under 500k", signals=en)
        assert rf is not None
        assert rf.price_max == 500_000

    def test_en_superlative_cheapest(self) -> None:
        en = get_routing_signals("en")
        rf = parse_range_query("which is the cheapest", signals=en)
        assert rf is not None
        assert rf.operation == "min"

    def test_en_list_keyword(self) -> None:
        en = get_routing_signals("en")
        rf = parse_list_query("list all massage services", signals=en)
        assert rf is not None
        assert rf.operation == "keyword"
        assert "massage" in rf.keyword.lower()

    def test_en_price_of_entity(self) -> None:
        en = get_routing_signals("en")
        rf = parse_price_of_entity_query("how much is laser hair removal", signals=en)
        assert rf is not None
        assert rf.operation == "keyword"

    def test_en_intent_greeting(self) -> None:
        en = get_routing_signals("en")
        assert classify_heuristic("hello there", signals=en).intent == "greeting"

    def test_en_intent_comparison(self) -> None:
        en = get_routing_signals("en")
        assert (
            classify_heuristic("compare A and B", signals=en).intent == "comparison"
        )

    def test_en_does_not_misroute_vietnamese_below(self) -> None:
        # An en bot has no "duoi" token → a VN phrase routes NOTHING (vector),
        # not a wrong range. Graceful, never mis-routes.
        en = get_routing_signals("en")
        assert parse_range_query("dưới 500k", signals=en) is None


# ---------------------------------------------------------------------------
# unknown / empty locale — graceful degradation, never crash / mis-route
# ---------------------------------------------------------------------------
class TestGracefulDegradation:
    def test_unknown_locale_falls_back_to_vi_seed(self) -> None:
        # No DB row, no in-memory pack → vi seed (DEFAULT_LANGUAGE), so a
        # deployment with a typo'd locale still works like vi (no crash).
        assert get_routing_signals("zz") is get_routing_signals("vi")

    def test_empty_signals_fires_no_route(self) -> None:
        empty = RoutingSignals()
        assert parse_range_query("dưới 500k", signals=empty) is None
        assert parse_list_query("liệt kê dịch vụ", signals=empty) is None
        assert parse_price_of_entity_query("giá bao nhiêu", signals=empty) is None

    def test_empty_signals_intent_none(self) -> None:
        empty = RoutingSignals()
        assert classify_heuristic("xin chào", signals=empty).intent is None

    def test_empty_query_never_crashes(self) -> None:
        for fn in (parse_range_query, parse_list_query, parse_price_of_entity_query):
            assert fn("") is None
            assert fn("   ") is None
        assert classify_heuristic("").intent is None


# ---------------------------------------------------------------------------
# JSON serde — DB ↔ seed parity (no drift)
# ---------------------------------------------------------------------------
class TestRoutingSignalsSerde:
    def test_vi_roundtrip_lossless(self) -> None:
        vi = get_routing_signals("vi")
        assert routing_signals_from_json(routing_signals_to_json(vi)) == vi

    def test_en_roundtrip_lossless(self) -> None:
        en = get_routing_signals("en")
        assert routing_signals_from_json(routing_signals_to_json(en)) == en

    def test_malformed_json_degrades_to_fallback(self) -> None:
        vi = get_routing_signals("vi")
        assert routing_signals_from_json("not json{", fallback=vi) == vi

    def test_empty_json_degrades_to_empty_default(self) -> None:
        # No fallback → empty-signal object, fires no route.
        sig = routing_signals_from_json("")
        assert sig == RoutingSignals()

    def test_partial_json_keeps_fallback_for_missing_fields(self) -> None:
        vi = get_routing_signals("vi")
        sig = routing_signals_from_json('{"below_tokens": ["under"]}', fallback=vi)
        assert sig.below_tokens == ("under",)
        # An unspecified field keeps the fallback's value.
        assert sig.above_tokens == vi.above_tokens

    def test_language_pack_from_dict_hydrates_routing_signals(self) -> None:
        en = get_routing_signals("en")
        rows = {"routing_signals": routing_signals_to_json(en)}
        pack = language_pack_from_dict("en", rows)
        assert pack.routing_signals == en

    def test_language_pack_from_dict_absent_keeps_seed(self) -> None:
        # No routing_signals row → keep the in-memory seed (vi byte-identical).
        pack = language_pack_from_dict("vi", {})
        assert pack.routing_signals == get_routing_signals("vi")


# ---------------------------------------------------------------------------
# alembic seed parity — migration row deserialises back to the in-memory seed
# ---------------------------------------------------------------------------
def _load_seed_migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "20260625_seed_routing_signals_lang_packs.py"
    )
    spec = importlib.util.spec_from_file_location(path.name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSeedMigrationParity:
    def test_migration_chains_off_head(self) -> None:
        mod = _load_seed_migration()
        assert mod.revision == "seed_routing_signals_260625"
        assert mod.down_revision == "rerank_provider_align_260625"
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)

    def test_seed_row_deserialises_to_inmemory_seed(self) -> None:
        mod = _load_seed_migration()
        seeded = {(c, k): v for c, k, v in mod._SEED_ROWS}
        assert routing_signals_from_json(
            seeded[("vi", "routing_signals")]
        ) == get_routing_signals("vi")
        assert routing_signals_from_json(
            seeded[("en", "routing_signals")]
        ) == get_routing_signals("en")
