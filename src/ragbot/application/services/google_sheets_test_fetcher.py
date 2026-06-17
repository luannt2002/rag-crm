# ============================================================
# DEAD-CODE NOTICE — 2026-06-03
# ============================================================
# This module is NOT reachable from any production entry point.
# Verified via:
#   * AST import-graph reachability scan (entry: FastAPI app +
#     workers + middlewares + routes)
#   * 10-agent multi-trace audit (Agent 9 vulture + Agent 10
#     runtime-path)
#
# Reason: Self-docstring: 'Production code MUST NOT import this module'. No reference anywhere.
#
# Status:
#   * Code kept INTACT (reversible — remove this header to reactivate)
#   * Safe to delete physically; defer to operator decision
#
# To reactivate:
#   1. Confirm a runtime caller is intentional (search registry
#      strings, dynamic imports)
#   2. Remove this header block
#   3. Wire the registry / DI binding in bootstrap.py
# ============================================================

# """Auto-discover all tabs of a Google spreadsheet — TEST FLOW ONLY.

# Real ingest flow stays user-paste-tab-only (no auto-discover) per user
# mandate only test fixtures should expand to all tabs to
# maximise corpus coverage; production users explicitly choose the tab(s)
# they want indexed.

# Usage:
#     from ragbot.application.services.google_sheets_test_fetcher import (
#         list_all_tabs,
#     )
#     tabs = await list_all_tabs(spreadsheet_id, api_key=os.environ["GOOGLE_API_KEY"])
    # tabs == [{"gid": 0, "title": "Sheet1"}, ...]

# Production code MUST NOT import this module — kept separate from
# ``google_link_service`` to make the boundary explicit at review time.
# """
# from __future__ import annotations

# from typing import Any

# import httpx
# import structlog

# from ragbot.shared.constants import (
#     DEFAULT_GOOGLE_SHEETS_TEST_MAX_TABS,
#     DEFAULT_GOOGLE_SHEETS_TEST_TIMEOUT_S,
# )

# logger = structlog.get_logger(__name__)

# _SHEETS_METADATA_URL = "https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
# _FIELDS_MASK = "sheets(properties(sheetId,title))"


# async def list_all_tabs(
#     spreadsheet_id: str,
#     api_key: str,
#     *,
#     timeout_s: int = DEFAULT_GOOGLE_SHEETS_TEST_TIMEOUT_S,
#     max_tabs: int = DEFAULT_GOOGLE_SHEETS_TEST_MAX_TABS,
#     http_client: httpx.AsyncClient | None = None,
# ) -> list[dict[str, Any]]:
#     """Return ``[{"gid": int, "title": str}, ...]`` for every tab in the sheet.

#     Wraps the Google Sheets v4 metadata endpoint with a tight ``fields``
#     mask so we never pull the actual cell data.

#     @param spreadsheet_id: the ``/d/<id>/`` segment of the spreadsheet URL.
#     @param api_key: Google API key with the Sheets read scope. The caller
#         is responsible for keeping this out of source control (env-driven).
#     @param timeout_s: HTTP timeout — kept tight because this only runs in
#         CI fixture setup.
#     @param max_tabs: defensive cap. Spreadsheets with more than this many
#         tabs are truncated (with a warn log) rather than silently dropping
#         rows downstream.
#     @param http_client: dependency-injected httpx client for tests; when
#         omitted a per-call client is created and closed.
#     @return: list of ``{"gid": int, "title": str}``. Empty when the API
#         responds with no sheets (e.g. unauthorised or revoked spreadsheet).
#     """
#     if not spreadsheet_id or not spreadsheet_id.strip():
#         return []
#     url = _SHEETS_METADATA_URL.format(spreadsheet_id=spreadsheet_id)
#     params = {"fields": _FIELDS_MASK, "key": api_key}

#     async def _do_get(client: httpx.AsyncClient) -> dict[str, Any]:
#         resp = await client.get(url, params=params)
#         resp.raise_for_status()
#         return resp.json()

#     if http_client is not None:
#         payload = await _do_get(http_client)
#     else:
#         async with httpx.AsyncClient(timeout=timeout_s) as client:
#             payload = await _do_get(client)

#     sheets = payload.get("sheets", []) or []
#     tabs: list[dict[str, Any]] = []
#     for entry in sheets:
#         props = entry.get("properties") or {}
#         sheet_id = props.get("sheetId")
#         title = props.get("title")
#         if sheet_id is None or title is None:
#             continue
#         tabs.append({"gid": int(sheet_id), "title": str(title)})
#         if len(tabs) >= max_tabs:
#             logger.warning(
#                 "google_sheets_test_max_tabs_reached",
#                 spreadsheet_id=spreadsheet_id,
#                 max_tabs=max_tabs,
#             )
#             break
#     logger.info(
#         "google_sheets_test_tabs_listed",
#         spreadsheet_id=spreadsheet_id,
#         count=len(tabs),
#     )
#     return tabs


# __all__ = ["list_all_tabs"]
