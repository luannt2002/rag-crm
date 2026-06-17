"""Pin test — pipeline_config builder tuple ↔ query_graph._pcfg call sites parity.

Bug #7 root cause (2026-05-25): per-intent dicts seeded via alembic
010x (``rerank_top_n_by_intent`` + ``generate_context_chars_cap_by_intent``)
were silently dropped because ``_PIPELINE_CFG_KEYS`` (test_chat.py) and
``_CHAT_CONFIG_KEYS`` (chat_worker.py) — the tuples that gate which
``system_config`` rows ``cfg_svc.get_many()`` batches — did not include
the new keys.

This test detects the pattern at code level:

  * Every key passed to ``_pcfg(state, "<key>", ...)`` in
    ``query_graph.py`` MUST appear in ``_build_pipeline_config`` body
    (the only thing that puts data into ``state["pipeline_config"]``).
  * ``_PIPELINE_CFG_KEYS`` + ``_CHAT_CONFIG_KEYS`` MUST stay in lockstep
    (the existing ``scripts/audit_pipeline_cfg_parity.py`` already
    checks this — promoted here so CI gates ship.

A miss in either guard makes a freshly-seeded ``system_config`` row
behave as if it were never set — the per-bot opt-in via ``plan_limits``
would still work but the global default would silently fall through to
the constant. That was the Phase 3 dead-code symptom.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUERY_GRAPH = _REPO_ROOT / "src" / "ragbot" / "orchestration" / "query_graph.py"
_TEST_CHAT = _REPO_ROOT / "src" / "ragbot" / "interfaces" / "http" / "routes" / "test_chat" / "_pipeline_config.py"
# chat_worker was split into a package; ``_CHAT_CONFIG_KEYS`` lives in the
# config sub-module.
_CHAT_WORKER = _REPO_ROOT / "src" / "ragbot" / "interfaces" / "workers" / "chat_worker" / "config.py"


# Keys that ``query_graph._pcfg`` reads but are intentionally NOT
# pipeline_config-built — they live in state but populated upstream of
# the builder (e.g. ``intent``, ``bot_created_at``). Add to allow-list
# only after verifying the state-set call site exists.
_PCFG_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Per-bot dynamic values resolved from bot_cfg, not system_config
        "rerank_intent_whitelist",  # built from bot.threshold_overrides
        "xml_wrap_enabled",          # resolved via _resolve_xml_wrap_enabled
        "bot_custom_vocabulary",     # populated from bot_cfg.custom_vocabulary
    },
)


def _extract_pcfg_keys(source_path: Path) -> set[str]:
    """Return the set of literal string keys passed to ``_pcfg(state, "<key>", ...)``.

    Skips f-string / variable args (those are flagged elsewhere).
    """
    text = source_path.read_text(encoding="utf-8")
    # Match `_pcfg(state, "<key>"` — single quotes also tolerated.
    pattern = re.compile(r'_pcfg\(\s*state\s*,\s*["\']([a-zA-Z_][\w.]*)["\']')
    return set(pattern.findall(text))


def _extract_tuple_keys(source_path: Path, tuple_name: str) -> set[str]:
    """Return the set of string literals inside ``<tuple_name>: tuple[...] = (...)``.

    The extractor walks lines starting at the tuple header until it finds
    a line whose stripped content is exactly ``)`` (the closing paren on
    its own line — Python style for multi-line tuple definitions). This
    avoids brittle paren-depth counting that breaks on parens inside
    string literals or trailing function-call statements after the tuple.
    """
    text = source_path.read_text(encoding="utf-8")
    open_marker = re.search(
        rf"^{re.escape(tuple_name)}\s*:\s*tuple\b.*?=\s*\(\s*$",
        text, re.MULTILINE,
    )
    if not open_marker:
        return set()
    start = open_marker.end()
    # Walk lines until we find a line that is just `)` (the close paren
    # at column 0, marking the end of the tuple definition).
    remaining = text[start:]
    close_match = re.search(r"^\)\s*$", remaining, re.MULTILINE)
    if not close_match:
        return set()
    body = remaining[: close_match.start()]
    return set(re.findall(r'["\']([a-zA-Z_][\w.]*)["\']', body))


def _extract_dict_keys(source_path: Path, marker_function: str) -> set[str]:
    """Return string-literal keys appearing as dict keys inside ``marker_function``.

    Scans the body of the named function for ``"<key>":`` patterns. The
    function ends when we hit a line whose stripped content is exactly
    ``}`` followed by the function-body terminator (no further indented
    lines belonging to this function). This avoids brittle brace-depth
    counting that mis-balances on dicts containing function-call values.
    """
    text = source_path.read_text(encoding="utf-8")
    m = re.search(rf"def\s+{re.escape(marker_function)}\b.*?return\s*\{{",
                  text, re.DOTALL)
    if not m:
        return set()
    start = m.end()
    remaining = text[start:]
    # Closing brace at column 4 (function body indent) marks end of dict.
    close_match = re.search(r"^    \}\s*$", remaining, re.MULTILINE)
    if not close_match:
        return set()
    body = remaining[: close_match.start()]
    return set(re.findall(r'["\']([a-zA-Z_][\w.]*)["\']\s*:', body))


# -- Cross-check pcfg keys -- vs builder dict body --------------------------


@pytest.fixture(scope="module")
def pcfg_keys() -> set[str]:
    return _extract_pcfg_keys(_QUERY_GRAPH)


@pytest.fixture(scope="module")
def test_chat_builder_keys() -> set[str]:
    return _extract_dict_keys(_TEST_CHAT, "_build_pipeline_config")


def test_query_graph_pcfg_keys_all_built_in_test_chat(
    pcfg_keys: set[str], test_chat_builder_keys: set[str],
) -> None:
    """Every key ``query_graph._pcfg`` reads must be populated by the
    test_chat ``_build_pipeline_config`` dict (modulo the allow-list)."""
    missing = pcfg_keys - test_chat_builder_keys - _PCFG_ALLOWLIST
    assert not missing, (
        "query_graph._pcfg reads these keys but _build_pipeline_config "
        "(test_chat.py) never populates them — call sites will silently "
        "fall back to the caller-supplied default forever.\n"
        f"Missing keys: {sorted(missing)!r}\n"
        "Fix: add an entry to _build_pipeline_config OR — if the key is "
        "populated elsewhere (e.g. from bot_cfg, JWT) — add it to "
        "_PCFG_ALLOWLIST in this test."
    )


# -- Tuple parity test_chat ↔ chat_worker ------------------------------------


@pytest.fixture(scope="module")
def test_chat_tuple() -> set[str]:
    return _extract_tuple_keys(_TEST_CHAT, "_PIPELINE_CFG_KEYS")


@pytest.fixture(scope="module")
def chat_worker_tuple() -> set[str]:
    return _extract_tuple_keys(_CHAT_WORKER, "_CHAT_CONFIG_KEYS")


def test_pipeline_cfg_keys_match_chat_worker(
    test_chat_tuple: set[str], chat_worker_tuple: set[str],
) -> None:
    """The two batched-load tuples MUST stay in lockstep.

    Drift between them is the exact failure mode Wave M3.1 sync (2026-
    05-20 commit 648920a) tried to close: a key added to chat_worker
    but missing from test_chat (or vice versa) silently makes the
    test/chat endpoint diverge from production behaviour.
    """
    only_test = test_chat_tuple - chat_worker_tuple
    only_worker = chat_worker_tuple - test_chat_tuple
    assert not only_test and not only_worker, (
        f"_PIPELINE_CFG_KEYS ↔ _CHAT_CONFIG_KEYS drift detected.\n"
        f"  only in test_chat.py: {sorted(only_test)!r}\n"
        f"  only in chat_worker.py: {sorted(only_worker)!r}\n"
        "Both tuples must mirror each other. Add the missing key(s) to "
        "the relevant tuple."
    )


# -- Per-intent dict keys MUST be batched ------------------------------------


def test_per_intent_keys_in_pipeline_cfg_tuple(
    test_chat_tuple: set[str], chat_worker_tuple: set[str],
) -> None:
    """Every ``*_by_intent`` JSONB row used by code MUST be in both tuples.

    Bug #7 is the canonical failure: ``rerank_top_n_by_intent`` and
    ``generate_context_chars_cap_by_intent`` were referenced in
    query_graph but the tuples skipped them → ``get_many()`` never
    loaded them → state["pipeline_config"] missing → ``_pcfg`` fell
    back to the caller default → per-intent boost never fired.
    """
    by_intent_keys_in_code = {
        k for k in _extract_pcfg_keys(_QUERY_GRAPH) if k.endswith("_by_intent")
    }
    missing_test = by_intent_keys_in_code - test_chat_tuple
    missing_worker = by_intent_keys_in_code - chat_worker_tuple
    assert not missing_test, (
        f"_by_intent keys read by code but missing from _PIPELINE_CFG_KEYS: "
        f"{sorted(missing_test)!r}"
    )
    assert not missing_worker, (
        f"_by_intent keys read by code but missing from _CHAT_CONFIG_KEYS: "
        f"{sorted(missing_worker)!r}"
    )
