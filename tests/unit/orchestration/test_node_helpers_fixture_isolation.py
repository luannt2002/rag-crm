"""Regression test: autouse fixture clears `_node_test_helpers` module lists.

`tests/unit/_node_test_helpers.py` keeps four module-level lists
(`_LAST_TEST_TRACKER`, `_LAST_TEST_KG_SERVICE`, `_LAST_TEST_SESSION_FACTORY`,
`_LAST_TEST_BOT_SYSTEM_PROMPT`) that `build_test_graph` appends to so
`make_state` can default to the same instance the test fixture is keeping
a handle on. Module-level state persists across tests in the same process,
so without an autouse cleanup an earlier test's appends leak into later
tests' `make_state` defaults.

This test relies on pytest test-execution order: the first test seeds the
lists, the second test must observe them empty (proving the autouse
fixture in `tests/conftest.py` clears them before each test).

Pinned naming `test_a_*` / `test_b_*` to make the order explicit even if
the file is later collected with a non-default ordering plugin.
"""

from __future__ import annotations

from tests.unit import _node_test_helpers


def test_a_seed_module_lists_with_sentinel_values() -> None:
    """Append distinct sentinel values to each of the 4 module-level lists.

    Real assertion: after appending, each list has exactly one element
    matching the sentinel — proves we can mutate the module state at all.
    """
    _node_test_helpers._LAST_TEST_TRACKER.append("sentinel-tracker")
    _node_test_helpers._LAST_TEST_KG_SERVICE.append("sentinel-kg")
    _node_test_helpers._LAST_TEST_SESSION_FACTORY.append("sentinel-sf")
    _node_test_helpers._LAST_TEST_BOT_SYSTEM_PROMPT.append("sentinel-bsp")

    assert _node_test_helpers._LAST_TEST_TRACKER == ["sentinel-tracker"]
    assert _node_test_helpers._LAST_TEST_KG_SERVICE == ["sentinel-kg"]
    assert _node_test_helpers._LAST_TEST_SESSION_FACTORY == ["sentinel-sf"]
    assert _node_test_helpers._LAST_TEST_BOT_SYSTEM_PROMPT == ["sentinel-bsp"]


def test_b_next_test_starts_with_empty_module_lists() -> None:
    """Autouse fixture in `tests/conftest.py` must have cleared all 4 lists.

    If state leaked from `test_a_*`, each list would still contain its
    sentinel string. Real assertion on emptiness — `len(...) == 0` and
    explicit `[] == ...` — fails loudly if the autouse fixture regresses.
    """
    assert _node_test_helpers._LAST_TEST_TRACKER == [], (
        f"_LAST_TEST_TRACKER leaked from prior test: "
        f"{_node_test_helpers._LAST_TEST_TRACKER!r}"
    )
    assert _node_test_helpers._LAST_TEST_KG_SERVICE == [], (
        f"_LAST_TEST_KG_SERVICE leaked from prior test: "
        f"{_node_test_helpers._LAST_TEST_KG_SERVICE!r}"
    )
    assert _node_test_helpers._LAST_TEST_SESSION_FACTORY == [], (
        f"_LAST_TEST_SESSION_FACTORY leaked from prior test: "
        f"{_node_test_helpers._LAST_TEST_SESSION_FACTORY!r}"
    )
    assert _node_test_helpers._LAST_TEST_BOT_SYSTEM_PROMPT == [], (
        f"_LAST_TEST_BOT_SYSTEM_PROMPT leaked from prior test: "
        f"{_node_test_helpers._LAST_TEST_BOT_SYSTEM_PROMPT!r}"
    )

    # Belt-and-braces: also confirm the four expected names still exist
    # on the helper module (catches accidental rename refactors).
    assert hasattr(_node_test_helpers, "_LAST_TEST_TRACKER")
    assert hasattr(_node_test_helpers, "_LAST_TEST_KG_SERVICE")
    assert hasattr(_node_test_helpers, "_LAST_TEST_SESSION_FACTORY")
    assert hasattr(_node_test_helpers, "_LAST_TEST_BOT_SYSTEM_PROMPT")


def test_c_isolation_holds_after_third_seed() -> None:
    """After `test_b_*` observed empty, seed again and the next assertion
    inside this same test still sees the values (intra-test mutations are
    not stomped — only the per-test reset boundary is enforced).
    """
    # Pre-condition: clean slate from autouse fixture.
    assert _node_test_helpers._LAST_TEST_TRACKER == []
    assert _node_test_helpers._LAST_TEST_KG_SERVICE == []
    assert _node_test_helpers._LAST_TEST_SESSION_FACTORY == []
    assert _node_test_helpers._LAST_TEST_BOT_SYSTEM_PROMPT == []

    _node_test_helpers._LAST_TEST_TRACKER.append("a")
    _node_test_helpers._LAST_TEST_TRACKER.append("b")
    _node_test_helpers._LAST_TEST_KG_SERVICE.append(object())

    # Within the same test, mutations stick.
    assert _node_test_helpers._LAST_TEST_TRACKER == ["a", "b"]
    assert len(_node_test_helpers._LAST_TEST_KG_SERVICE) == 1


def test_d_clean_slate_after_third_seed() -> None:
    """After `test_c_*` left two trackers and one kg_service appended,
    autouse fixture must again present empty lists at the start.
    """
    assert _node_test_helpers._LAST_TEST_TRACKER == []
    assert _node_test_helpers._LAST_TEST_KG_SERVICE == []
    assert _node_test_helpers._LAST_TEST_SESSION_FACTORY == []
    assert _node_test_helpers._LAST_TEST_BOT_SYSTEM_PROMPT == []
