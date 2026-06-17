"""Unit tests — ``scripts/manage_bot_vocabulary.py`` CRUD logic + arg parsing.

Phase C / Stream C4 (owner corpus enrichment CLI).

The DB / Redis I/O paths are covered indirectly via the pure helpers
(``upsert_category``, ``remove_category``, ``summarise``,
``parse_json_payload``). Heavy I/O is exercised by integration tests in
the smoke suite; here we focus on the deterministic algebra that decides
WHAT the next ``custom_vocabulary`` JSONB blob will look like — that is
where bugs would silently corrupt bot state.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "manage_bot_vocabulary.py"


def _load_script_module():
    """Load the CLI script as a module (it lives in scripts/, not in src/)."""
    spec = importlib.util.spec_from_file_location(
        "manage_bot_vocabulary", _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["manage_bot_vocabulary"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _load_script_module()


# ---------------------------------------------------------------------------
# upsert_category — preserves existing keys, supports multiple categories.
# ---------------------------------------------------------------------------


def test_upsert_adds_to_existing_category_without_dropping_keys(cli):
    current = {"abbreviations": {"sđt": "số điện thoại"}}
    new = cli.upsert_category(current, "abbreviations", {"đt": "điện thoại"})

    assert new["abbreviations"] == {
        "sđt": "số điện thoại",
        "đt": "điện thoại",
    }
    # Original must NOT be mutated — the function is pure.
    assert current == {"abbreviations": {"sđt": "số điện thoại"}}


def test_upsert_creates_new_category_when_absent(cli):
    current = {"abbreviations": {"sđt": "số điện thoại"}}
    new = cli.upsert_category(current, "diacritics", {"truong hop": "trường hợp"})

    assert new["abbreviations"] == {"sđt": "số điện thoại"}
    assert new["diacritics"] == {"truong hop": "trường hợp"}


def test_upsert_overrides_existing_key_in_same_category(cli):
    current = {"synonyms": {"sđt": ["số điện thoại"]}}
    new = cli.upsert_category(
        current, "synonyms", {"sđt": ["số điện thoại", "đt"]},
    )

    assert new["synonyms"]["sđt"] == ["số điện thoại", "đt"]


def test_upsert_rejects_non_string_keys(cli):
    with pytest.raises(ValueError, match="payload keys must be str"):
        cli.upsert_category({}, "abbreviations", {123: "foo"})


def test_upsert_replaces_non_dict_value_with_proper_dict(cli):
    # Bad data left in the column (someone wrote a string under
    # "abbreviations"). Upsert must replace it cleanly with a proper dict.
    current = {"abbreviations": "stale-string-value"}
    new = cli.upsert_category(current, "abbreviations", {"sđt": "số điện thoại"})

    assert new["abbreviations"] == {"sđt": "số điện thoại"}


# ---------------------------------------------------------------------------
# remove_category — whole-bucket + single-key deletion.
# ---------------------------------------------------------------------------


def test_remove_whole_category_returns_entry_count(cli):
    current = {
        "abbreviations": {"sđt": "số điện thoại", "đt": "điện thoại"},
        "diacritics": {"truong hop": "trường hợp"},
    }
    new, removed = cli.remove_category(current, "abbreviations")

    assert removed == 2
    assert "abbreviations" not in new
    assert new["diacritics"] == {"truong hop": "trường hợp"}


def test_remove_single_key_inside_category(cli):
    current = {
        "abbreviations": {"sđt": "số điện thoại", "đt": "điện thoại"},
    }
    new, removed = cli.remove_category(current, "abbreviations", key="đt")

    assert removed == 1
    assert new["abbreviations"] == {"sđt": "số điện thoại"}


def test_remove_missing_category_is_noop(cli):
    current = {"abbreviations": {"sđt": "số điện thoại"}}
    new, removed = cli.remove_category(current, "synonyms")

    assert removed == 0
    assert new == current
    # Returned dict must be a copy, not the same object.
    assert new is not current


def test_remove_missing_key_in_existing_category_is_noop(cli):
    current = {"abbreviations": {"sđt": "số điện thoại"}}
    new, removed = cli.remove_category(current, "abbreviations", key="nonexistent")

    assert removed == 0
    assert new["abbreviations"] == {"sđt": "số điện thoại"}


# ---------------------------------------------------------------------------
# summarise — listing output shape.
# ---------------------------------------------------------------------------


def test_summarise_counts_entries_per_category(cli):
    current = {
        "abbreviations": {"a": "x", "b": "y"},
        "synonyms": {"c": ["x"]},
        "diacritics": {},
    }
    counts = cli.summarise(current)

    assert counts == {"abbreviations": 2, "synonyms": 1, "diacritics": 0}


def test_summarise_handles_non_dict_input_as_empty(cli):
    assert cli.summarise(None) == {}  # type: ignore[arg-type]
    assert cli.summarise("not a dict") == {}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# parse_json_payload — input validation for the ``set`` subcommand.
# ---------------------------------------------------------------------------


def test_parse_json_payload_accepts_object(cli):
    out = cli.parse_json_payload('{"sđt": "số điện thoại"}')

    assert out == {"sđt": "số điện thoại"}


def test_parse_json_payload_rejects_array(cli):
    with pytest.raises(ValueError, match="JSON payload must be an object"):
        cli.parse_json_payload('["sđt", "số điện thoại"]')


def test_parse_json_payload_rejects_malformed_json(cli):
    with pytest.raises(ValueError, match="invalid JSON"):
        cli.parse_json_payload("{not json}")


# ---------------------------------------------------------------------------
# CLI arg parser — subcommand wiring (regression guard).
# ---------------------------------------------------------------------------


def test_argparser_set_requires_payload_and_category(cli):
    parser = cli.build_parser()

    args = parser.parse_args([
        "set", "bot-123", "abbreviations", '{"sđt": "số điện thoại"}',
        "--channel-type", "web", "--confirm",
    ])

    assert args.cmd == "set"
    assert args.bot_id == "bot-123"
    assert args.category == "abbreviations"
    assert args.json_payload == '{"sđt": "số điện thoại"}'
    assert args.channel_type == "web"
    assert args.confirm is True
    assert args.tenant_uuid is None


def test_argparser_remove_supports_optional_key(cli):
    parser = cli.build_parser()

    args = parser.parse_args([
        "remove", "bot-123", "abbreviations",
        "--key", "sđt", "--channel-type", "web",
    ])

    assert args.cmd == "remove"
    assert args.category == "abbreviations"
    assert args.key == "sđt"
    assert args.confirm is False  # default dry-run


def test_argparser_get_category_choice_enforced(cli):
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args([
            "get", "bot-123", "made_up_category", "--channel-type", "web",
        ])


def test_known_categories_match_orchestrator_keys(cli):
    # Regression: if the orchestrator starts reading a new top-level key,
    # the CLI must learn it too. Currently the orchestrator reads
    # "abbreviations" and "synonyms" + "diacritics" via query_graph.py.
    # The CLI also exposes "typo_corrections" as forward-compat.
    assert "abbreviations" in cli.KNOWN_CATEGORIES
    assert "synonyms" in cli.KNOWN_CATEGORIES
    assert "diacritics" in cli.KNOWN_CATEGORIES
    assert "typo_corrections" in cli.KNOWN_CATEGORIES
