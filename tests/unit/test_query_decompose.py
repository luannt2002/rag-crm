"""Tests for P15-6 query decomposition parsing."""

from __future__ import annotations

from ragbot.orchestration.query_graph import parse_decomposed_sub_queries


class TestDecomposeParse:
    def test_valid_two_subs(self):
        raw = '["Giá A là gì?", "Giá B là gì?"]'
        assert parse_decomposed_sub_queries(raw) == ["Giá A là gì?", "Giá B là gì?"]

    def test_valid_four_subs(self):
        raw = '["Q1", "Q2", "Q3", "Q4"]'
        assert parse_decomposed_sub_queries(raw) == ["Q1", "Q2", "Q3", "Q4"]

    def test_trims_to_max_sub(self):
        raw = '["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"]'
        # Default max_sub=4 → only first 4 kept
        assert parse_decomposed_sub_queries(raw) == ["Q1", "Q2", "Q3", "Q4"]

    def test_custom_max_sub(self):
        raw = '["Q1", "Q2", "Q3"]'
        assert parse_decomposed_sub_queries(raw, max_sub=2) == ["Q1", "Q2"]

    def test_strips_whitespace(self):
        raw = '["  spaced  ", "\\ttabbed\\n"]'
        result = parse_decomposed_sub_queries(raw)
        assert result == ["spaced", "tabbed"]

    def test_drops_empty_strings(self):
        raw = '["real", "", "  ", "another"]'
        # After strip, empties filtered out
        assert parse_decomposed_sub_queries(raw) == ["real", "another"]

    def test_single_item_returns_empty(self):
        # A single-question array is not a decomposition — don't split
        assert parse_decomposed_sub_queries('["only one"]') == []

    def test_empty_array(self):
        assert parse_decomposed_sub_queries("[]") == []

    def test_non_array_json(self):
        assert parse_decomposed_sub_queries('{"q": "x"}') == []

    def test_non_json_prose(self):
        raw = "The two sub-questions are: 1) X, 2) Y"
        assert parse_decomposed_sub_queries(raw) == []

    def test_malformed_json(self):
        assert parse_decomposed_sub_queries('["unterminated') == []

    def test_empty_input(self):
        assert parse_decomposed_sub_queries("") == []
        assert parse_decomposed_sub_queries("   ") == []

    def test_coerces_non_string_items(self):
        # LLM sometimes returns numbers or dicts; coerce to str
        raw = '[123, "text", true]'
        result = parse_decomposed_sub_queries(raw)
        # All coerced; 'True' not empty, kept
        assert result == ["123", "text", "True"]
