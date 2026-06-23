"""Monitoring + seed/reinit + link-validation routes for the test_chat package.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). The
``reinit-bots`` route re-runs the validated ``add_document`` path from the
document_routes sibling — a one-way dependency (document_routes never imports
this module), so the package import graph stays acyclic.
"""

from __future__ import annotations

import pathlib as _pathlib

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text  # noqa: F401 — re-export parity / monitoring SQL

from ragbot.application.services import google_link_service

from .schemas import AddDocumentRequest, ValidateLinkRequest
from .document_routes import add_document
from ._shared import (
    _find_bot_uuid,
    _require_owner,
    _sf,
)

router = APIRouter(tags=["test"])


# ── One-click seed / re-upload for the demo bots ───────────────────────────
# Single source of truth: tests/scenarios/bot_sources.json (bot_id +
# channel_type + workspace_id + the fixed HTTPS document URLs). The seed
# endpoint reads it and re-runs the SAME validated add_document path per URL,
# so the UI never drifts from the per-step manual flow.
_BOT_SOURCES_PATH = (
    _pathlib.Path(__file__).resolve().parents[6]
    / "tests" / "scenarios" / "bot_sources.json"
)


def _load_bot_sources() -> dict:
    import json as _json  # noqa: PLC0415
    return _json.loads(_BOT_SOURCES_PATH.read_text(encoding="utf-8"))


@router.get("/seed-sources")
async def seed_sources() -> dict:
    """The fixed 9-file seed config (bot_id, channel, workspace, URLs).

    Single source of truth the re-upload UI + eval scenarios both read, so the
    3-bot / 3-workspace setup can never drift between FE and BE.
    """
    return _load_bot_sources()


@router.post("/reinit-bots")
async def reinit_bots(request: Request, bot: str = "all", wipe: bool = True) -> dict:
    """One call: re-seed the demo bots from the fixed 9 URLs.

    For each target bot (``bot=all`` or a single slug): optionally wipe its
    existing documents+chunks, then re-run the validated ``add_document`` path
    for every fixed URL — passing the bot's ``workspace_id`` so each bot lands
    in its own workspace. Returns a per-document status summary; never raises on
    one bad URL (records the error and continues).
    """
    # Wipes document_chunks + documents for the demo bots — owner-only, matching
    # the sibling ``monitoring`` route's gate.
    _require_owner(request)
    from sqlalchemy import text as _sql_text  # noqa: PLC0415
    src = _load_bot_sources()
    sf = _sf(request)
    out: dict = {"bots": []}
    for b in src.get("bots", []):
        bid, ch, ws = b["bot_id"], b["channel_type"], b.get("workspace_id", "")
        if bot not in ("all", bid):
            continue
        bot_uuid = await _find_bot_uuid(request, bid, ch, workspace_id=ws)
        if wipe:
            async with sf() as session:
                await session.execute(_sql_text(
                    "DELETE FROM document_chunks WHERE record_bot_id = :b"
                ), {"b": bot_uuid})
                await session.execute(_sql_text(
                    "DELETE FROM documents WHERE record_bot_id = :b"
                ), {"b": bot_uuid})
                await session.commit()
        docs_out = []
        for d in b.get("documents", []):
            req = AddDocumentRequest(
                title=d["title"], url=d["url"], workspace_id=ws,
            )
            try:
                r = await add_document(bid, ch, req, request)
                docs_out.append({"title": d["title"], "status": r.get("status"),
                                 "document_id": r.get("document_id")})
            except HTTPException as exc:
                docs_out.append({"title": d["title"], "status": "error",
                                 "error": str(exc.detail)[:200]})
        out["bots"].append({"bot_id": bid, "workspace_id": ws,
                            "wiped": wipe, "documents": docs_out})
    return out


@router.post("/validate-link")
async def validate_link(req: ValidateLinkRequest) -> dict:
    """Kiểm tra tính hợp lệ của link Google Docs/Sheets.
    @param req: URL cần kiểm tra
    @return: {ok, type, access} hoặc {ok: false, error}
    """
    result = await google_link_service.validate_link(req.url)
    if not result.ok:
        return {"ok": False, "error": result.error}
    return {"ok": True, "type": result.doc_type, "access": result.access}


@router.get("/monitoring")
async def monitoring(
    request: Request,
    bot_id: str | None = None,
    limit: int | None = None,
    days: int | None = None,
) -> dict:
    """Durable per-request monitoring — start/finish/duration + tokens + cost.

    Reads the append-only ``monitoring_log`` (alembic 0217) which survives bot
    deletion / per-bot clear, so day-by-day cost + usage can always be audited.

    @param bot_id: optional filter by bot slug
    @param limit: recent rows to return (default 100, cap 1000)
    @param days: rollup window in days (default 30)
    @return: {ok, recent:[...], by_day:[...], by_bot:[...]}
    """
    _require_owner(request)
    _lim = max(1, min(int(limit or 100), 1000))
    _days = max(1, min(int(days or 30), 365))
    _bot = (bot_id or "").strip() or None
    sf = _sf(request)
    async with sf() as session:
        recent = (await session.execute(text("""
            SELECT bot_id, workspace_id, started_at, finished_at, duration_ms,
                   prompt_tokens, completion_tokens, total_tokens, cost_usd,
                   model_name, status
            FROM monitoring_log
            WHERE (CAST(:bot AS text) IS NULL OR bot_id = CAST(:bot AS text))
            ORDER BY started_at DESC NULLS LAST
            LIMIT CAST(:lim AS int)
        """), {"bot": _bot, "lim": _lim})).fetchall()
        by_day = (await session.execute(text("""
            SELECT date_trunc('day', started_at) AS day, count(*) n,
                   sum(total_tokens) tokens, round(sum(cost_usd)::numeric, 5) cost,
                   round(avg(duration_ms)) avg_ms
            FROM monitoring_log
            WHERE started_at > now() - make_interval(days => CAST(:d AS int))
              AND (CAST(:bot AS text) IS NULL OR bot_id = CAST(:bot AS text))
            GROUP BY day ORDER BY day DESC
        """), {"d": _days, "bot": _bot})).fetchall()
        by_bot = (await session.execute(text("""
            SELECT bot_id, count(*) n, sum(total_tokens) tokens,
                   round(sum(cost_usd)::numeric, 5) cost, round(avg(duration_ms)) avg_ms
            FROM monitoring_log
            WHERE started_at > now() - make_interval(days => CAST(:d AS int))
            GROUP BY bot_id ORDER BY cost DESC NULLS LAST
        """), {"d": _days})).fetchall()

    def _row(r):
        return {
            "bot_id": r[0], "workspace_id": r[1],
            "started_at": r[2].isoformat() if r[2] else None,
            "finished_at": r[3].isoformat() if r[3] else None,
            "duration_ms": r[4], "prompt_tokens": r[5], "completion_tokens": r[6],
            "total_tokens": r[7], "cost_usd": float(r[8]) if r[8] is not None else 0.0,
            "model_name": r[9], "status": r[10],
        }
    return {
        "ok": True,
        "recent": [_row(r) for r in recent],
        "by_day": [{"day": r[0].isoformat() if r[0] else None, "requests": r[1],
                    "tokens": int(r[2] or 0), "cost_usd": float(r[3] or 0),
                    "avg_duration_ms": int(r[4] or 0)} for r in by_day],
        "by_bot": [{"bot_id": r[0], "requests": r[1], "tokens": int(r[2] or 0),
                    "cost_usd": float(r[3] or 0), "avg_duration_ms": int(r[4] or 0)}
                   for r in by_bot],
    }


__all__ = [
    "router",
    "seed_sources",
    "reinit_bots",
    "validate_link",
    "monitoring",
]
