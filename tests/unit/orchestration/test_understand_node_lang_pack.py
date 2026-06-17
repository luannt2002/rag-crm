"""Regression test for mega-sprint-G19 — understand_query history routing
goes through the LanguagePack (i18n), not hardcoded Vietnamese literals.

Bug: ``query_graph.understand_query`` formatted history turns as
``f"{'Khách' if role == 'user' else 'Bot'}: ..."`` — a hardcoded VN
``"Khách"`` (Customer) and English ``"Bot"`` literal. This breaks
multi-locale bots (English / lang_pack overrides) and violates the
domain-neutral rule (CLAUDE.md). The sibling ``condense_question`` node
already uses ``_pack.condense_user_role`` / ``_pack.condense_bot_role``;
this node was the only divergence.

Fix: route through ``_lang(state).condense_user_role`` /
``condense_bot_role`` so per-bot ``language_pack`` overrides apply
(Vietnamese default = "Khách"/"Bot", English default = "User"/"Bot",
custom-locale rows override both).

Pre-fix: the hardcoded ``'Khách'``/``'Bot'`` substring is present.
Post-fix: the substring is gone and the ``LanguagePack`` attrs are read.
"""
from __future__ import annotations

import inspect
from pathlib import Path

# Read the source FILE directly (not via inspect) so we audit the exact
# narrow line region without dragging the heavy graph-build closure into
# import scope. Both regression statements operate on a small slice.

_QG_PATH = (
    Path(__file__).resolve().parents[3]
    / "src" / "ragbot" / "orchestration" / "query_graph.py"
)

# The ``understand_query`` node body was lifted out of ``build_graph`` into
# ``orchestration/nodes/understand.py``; ``condense_question`` still lives in
# query_graph. Audit BOTH the orchestrator wiring file and every extracted
# node module so the domain-neutral / LanguagePack regression guards survive
# the structural carve.
_NODES_DIR = (
    Path(__file__).resolve().parents[3]
    / "src" / "ragbot" / "orchestration" / "nodes"
)


def _orchestration_src() -> str:
    """Concatenated source of query_graph.py + every node module."""
    parts = [_QG_PATH.read_text(encoding="utf-8")]
    parts.extend(
        p.read_text(encoding="utf-8") for p in sorted(_NODES_DIR.glob("*.py"))
    )
    return "\n".join(parts)


def test_no_hardcoded_khach_bot_literal_in_understand_node() -> None:
    """``understand_query`` history-format must NOT carry hardcoded VN literal."""
    src = _orchestration_src()
    # The exact buggy expression that previously formatted history rows
    # in the understand_query node. If this string is ever re-added,
    # the regression slips back in.
    assert "'Khách' if m.get('role') == 'user' else 'Bot'" not in src, (
        "query_graph.py must not hardcode 'Khách'/'Bot' role labels — "
        "route through _lang(state).condense_user_role / condense_bot_role "
        "(LanguagePack i18n) per CLAUDE.md domain-neutral rule."
    )


def test_understand_node_uses_lang_pack_condense_role_attrs() -> None:
    """Confirms LanguagePack-driven roles are wired into the condense path."""
    src = _orchestration_src()
    # Both attrs must appear in the formatted f-string family — they are
    # consumed by both ``condense_question`` (already correct pre-G19)
    # and ``understand_query`` (G19 fix). Guard ensures G19 wiring is
    # in place even if a future refactor renames the local variable.
    assert "condense_user_role" in src
    assert "condense_bot_role" in src
    # Strong: must appear more than once — once for condense_question,
    # at least once for understand_query (post-G19).
    assert src.count("condense_user_role") >= 2, (
        "After mega-sprint-G19 both condense_question AND "
        "understand_query history-format must reference "
        "LanguagePack.condense_user_role; only one occurrence "
        "suggests the fix was reverted."
    )


def test_lang_pack_exposes_required_role_fields() -> None:
    """Defence-in-depth: LanguagePack contract must keep these attrs.

    If a future schema refactor drops ``condense_user_role`` /
    ``condense_bot_role``, the G19 fix would silently AttributeError
    at request time. This test catches the contract drift early.
    """
    from ragbot.shared.i18n import LanguagePack

    fields = set(inspect.signature(LanguagePack).parameters.keys())
    assert "condense_user_role" in fields
    assert "condense_bot_role" in fields
