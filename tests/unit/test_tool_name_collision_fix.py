"""ING-01 regression — tool_name derivation must not collide distinct docs.

``tool_name`` is the document's unique key (``uq_doc_tool`` = tenant + bot +
tool_name) and the ingest upsert is ``ON CONFLICT DO UPDATE`` — so two docs that
derive the SAME tool_name silently overwrite each other (data loss). The old
``title.lower().replace(" ", "_")[:64]`` blind-truncated, so two DISTINCT long
titles sharing a 64-char prefix collapsed into one row.

Pins:
    1. Short/normal titles derive to their plain normalized form (unchanged →
       idempotent re-ingest, zero migration churn).
    2. Two distinct long titles sharing a 64-char prefix derive DISTINCT names.
    3. Derivation is deterministic (same title → same name → re-ingest updates).
    4. The result never exceeds the identity budget.
"""

from __future__ import annotations

from ragbot.application.services.document_service.ingest_helpers import (
    derive_tool_name,
)
from ragbot.shared.constants import DEFAULT_TOOL_NAME_MAX_CHARS


def test_short_title_unchanged_normalized_form():
    """A within-budget title keeps its plain normalized form (back-compat)."""
    assert derive_tool_name("Bảng giá dịch vụ") == "bảng_giá_dịch_vụ"
    assert derive_tool_name("Price List") == "price_list"


def test_distinct_long_titles_do_not_collide():
    """Two different titles sharing a >64-char prefix must derive distinct
    tool_names — the old ``[:64]`` truncation collapsed them into one."""
    shared_prefix = "chính sách bảo hành và đổi trả sản phẩm cho khách hàng " * 2
    a = shared_prefix + " phần A khu vực miền bắc"
    b = shared_prefix + " phần B khu vực miền nam"
    assert len(a.replace(" ", "_")) > DEFAULT_TOOL_NAME_MAX_CHARS
    name_a = derive_tool_name(a)
    name_b = derive_tool_name(b)
    assert name_a != name_b, "distinct long titles collapsed to the same tool_name"


def test_derivation_is_deterministic():
    """Same title → same tool_name (re-ingest of the SAME doc still updates)."""
    long_title = "điều khoản sử dụng dịch vụ và chính sách quyền riêng tư áp dụng toàn hệ thống"
    assert derive_tool_name(long_title) == derive_tool_name(long_title)


def test_result_within_budget():
    """Derived name never exceeds the identity budget."""
    long_title = "x" * 500
    assert len(derive_tool_name(long_title)) <= DEFAULT_TOOL_NAME_MAX_CHARS
