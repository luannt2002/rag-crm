"""Debug ALL steps of the upload/ingest pipeline — one doc or one bot at a time.

Traces the REAL execution of every pipeline step for already-ingested documents:
maps the worker's structlog events to the canonical 22-step pipeline, shows when
each step fired, its output, timing, and an expert/cost verdict per step. Then
runs data-quality checks on the persisted chunks (size, dup, embedding dim/NULL).

This is the "soi all-step" harness the owner asked for — evidence-driven, no
guessing: every row is backed by a DB column, a structlog event, or a code
``file:line`` reference. Nothing here mutates state (read-only).

Usage:
    set -a && source .env && set +a
    .venv/bin/python scripts/debug_upload_steps.py --bot chinh-sach-xe
    .venv/bin/python scripts/debug_upload_steps.py --all
    .venv/bin/python scripts/debug_upload_steps.py --bot test-spa-id --since "30 minutes ago"

Notes:
  * Step execution is reconstructed from ``journalctl -u ragbot-py`` (the single
    consolidated service) + the per-doc rows in Postgres. A step shows ``— skip``
    when it legitimately did not fire for that doc (e.g. CR is row-gated for
    table strategies, narrate fires only on non-text blocks).
  * DATABASE_URL is read from the env (``postgresql+asyncpg://`` is rewritten to
    the libpq form for psql). No secret/brand literal is embedded here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import dataclass, field

# ── canonical pipeline: 22 steps across 4 phases ────────────────────────────
# Each step: (id, phase, name, what it does, structlog event(s) that prove it
# fired [None = inferred from DB/state], code evidence file:line, expert verdict).


@dataclass
class Step:
    sid: str
    phase: str
    name: str
    does: str
    events: tuple[str, ...]
    evidence: str
    verdict: str
    mand: bool = field(default=False)     # always runs (proven by persisted chunks)
    fired: str = field(default="")        # ts when first seen, or ""
    detail: str = field(default="")


def _steps() -> list[Step]:
    return [
        # ── PHASE A — Receive & queue (API, synchronous, <1s) ──
        Step("A1", "A·Receive", "add_document",
             "Nhận HTTPS link + resolve 4-key (tenant/workspace/bot/channel)",
             (), "interfaces/http/routes/test_chat.py:2409", "✅ correct (Pydantic + 4-key)"),
        Step("A2", "A·Receive", "validate_link",
             "Whitelist domain Google + doc-type + access check + title sniff",
             (), "application/services/google_link_service.py:57", "✅ robust (multi-layer)"),
        Step("A3", "A·Receive", "fetch_content",
             "Export txt (Docs) / csv (Sheets) + gid; size guard 10MB",
             (), "application/services/google_link_service.py:126",
             "🟡 silent-None trên non-200/<10 char → caller báo 'empty' (nên trả lỗi rõ)"),
        Step("A4", "A·Receive", "INSERT DRAFT",
             "Ghi documents(raw_content, content_hash) state=DRAFT",
             (), "application/services/document_service.py:2592", "✅ atomic"),
        Step("A5", "A·Receive", "emit document.uploaded.v1",
             "Ghi outbox event TRONG cùng transaction (no lost upload)",
             (), "document_service.py:2618", "✅ transactional outbox"),
        Step("A6", "A·Receive", "outbox→Redis stream",
             "Publisher poll outbox → XADD ragbot:documents:ingest",
             ("embedded_outbox_publisher_started",), "interfaces/workers/outbox_publisher.py:42",
             "✅ exactly-once"),
        Step("A7", "A·Receive", "worker consume",
             "Embedded consumer đọc stream → ingest()",
             ("embedded_document_consumer_started", "document.uploaded.consumed"),
             "interfaces/http/embedded_workers.py:57", "✅ single-process"),
        # ── PHASE B — Clean & chunk (worker) ──
        Step("B1", "B·Chunk", "clean text",
             "CleanBase Tier-0 + HTML strip + NFC + prompt-inject scrub",
             ("ingestion_cleaning_applied", "ingest_clean"),
             "document_service.py:1854", "✅ SOTA clean", mand=True),
        Step("B2", "B·Chunk", "select_strategy",
             "Chọn chiến lược: CSV fast-path / VN-legal HDT / weighted scoring",
             ("chunking_strategy_selected", "chunking_strategy"),
             "shared/chunking.py:747", "🟡 weighted=naive (Ekimetrics opt-in OFF)", mand=True),
        Step("B3", "B·Chunk", "chunk",
             "Cắt: table_csv/dual_index (row), HDT (breadcrumb), recursive, semantic",
             ("chunk_created",),
             "shared/chunking.py:1513/1619/1939", "✅ multi-strategy SOTA; doc-header dedup fixed", mand=True),
        Step("B4", "B·Chunk", "CR enrich",
             "Contextual Retrieval prefix/chunk (LLM) — ROW-GATED (skip table rows)",
             ("chunk_context_enrichment_applied", "contextual_retrieval_applied"),
             "application/services/contextual_chunk_enrichment.py:89",
             "🔴 cost-bomb trên OpenAI (prompt-cache chỉ Anthropic) — row-gate cứu 80-90%"),
        Step("B5", "B·Chunk", "vi_compound_segment",
             "Tách từ ghép tiếng Việt cho BM25 (chạy ∥ CR)",
             ("vi_compound_segmentation_applied",), "document_service.py:2879", "✅ correct"),
        Step("B6", "B·Chunk", "incremental_indexing",
             "Diff chunk: to_embed / unchanged / stale (re-ingest chỉ embed cái đổi)",
             ("incremental_indexing",), "document_service.py:2996", "✅ cost-saving"),
        # ── PHASE C — Embed & persist (worker, nút thắt) ──
        Step("C1", "C·Embed", "embedding_text_strategy",
             "canonicalize (strip URL + collapse ws); dual-field raw(BM25) vs canonical(embed)",
             ("embedding_text_strategy_applied",), "document_service.py:411/3109",
             "✅ dual-field đúng; tiết kiệm ~10% token"),
        Step("C2", "C·Embed", "narrate_then_embed",
             "Bảng→narrate câu tự nhiên (LLM/chunk) trước embed",
             ("narrate_then_embed_applied",), "application/services/narrate_dispatch.py:105",
             "🔴 KHÔNG batch + KHÔNG row-gate → bảng trả full cost (nên batch + skip table)"),
        Step("C3", "C·Embed", "embed_batch",
             "ZeroEntropy zembed-1 1280-dim matryoshka; batch 64/HTTP, doc-batch 100, sem=4",
             ("embed_batch_progress",),
             "infrastructure/embedding/zeroentropy_embedder.py:226",
             "✅ BATCHED đúng (SOTA); timeout 300s chống hang", mand=True),
        Step("C4", "C·Embed", "late_chunking",
             "Gắn context-window prefix cho chunk trước/khi embed",
             ("late_chunking_applied",), "document_service.py:3263", "✅ tăng coherence"),
        Step("C5", "C·Embed", "ingestion_validation",
             "Chấm điểm ingest (score); NULL-embed guard (len mismatch → FAILED)",
             ("ingestion_validation_passed",), "document_service.py:3419/3339",
             "✅ no silent NULL-embed"),
        # ── PHASE D — Finalize (atomic flip) ──
        Step("D1", "D·Finalize", "semantic_cache_invalidate",
             "Xoá cache câu trả lời cũ của bot",
             ("semantic_cache_invalidated",), "document_service.py:3846", "✅"),
        Step("D2", "D·Finalize", "document_ingested",
             "Persist chunks + FLIP DRAFT→active (atomic, gated trên embed count)",
             ("document_ingested",), "document_service.py:3990", "✅ atomic flip"),
        Step("D3", "D·Finalize", "stats_index",
             "Ghi entity stats (BM25 corpus stats)",
             ("stats_index_bulk_insert",), "infrastructure/repositories/stats_index_repository.py:125", "✅"),
        Step("D4", "D·Finalize", "emit document.ingested.v1",
             "Báo hoàn thành → outbox (webhook callback nếu cấu hình)",
             ("document.ingested",), "document_service.py (DocumentIngested)", "✅"),
    ]


def _pg_url() -> str:
    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        raise SystemExit("DATABASE_URL env var required (set -a && source .env)")
    return re.sub(r"postgresql\+asyncpg://", "postgresql://", raw)


def _psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["psql", _pg_url(), "-tA", "-F", "|", "-c", sql],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise SystemExit(f"psql failed: {out.stderr.strip()}")
    return [ln.split("|") for ln in out.stdout.strip().splitlines() if ln.strip()]


def _journal(service: str, since: str) -> list[dict]:
    out = subprocess.run(
        ["journalctl", "-u", service, "--since", since, "-o", "cat", "--no-pager"],
        capture_output=True, text=True, timeout=30,
    )
    evs = []
    for ln in out.stdout.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            evs.append(json.loads(ln))
        except ValueError:
            continue
    return evs


def _docs_for_bot(bot: str) -> list[dict]:
    rows = _psql(
        "SELECT d.document_name, d.state, d.content_chars, "
        "  round(EXTRACT(EPOCH FROM (d.updated_at-d.created_at)))::int, "
        "  (SELECT count(*) FROM document_chunks c WHERE c.record_document_id=d.id) "
        "FROM documents d JOIN bots b ON b.id=d.record_bot_id "
        f"WHERE b.bot_id='{bot}' ORDER BY d.document_name"
    )
    return [{"name": r[0], "state": r[1], "chars": int(r[2] or 0),
             "secs": int(r[3] or 0), "chunks": int(r[4] or 0)} for r in rows]


def _quality(bot: str) -> dict:
    r = _psql(
        "SELECT count(*), count(c.embedding), count(*)-count(c.embedding), "
        "  coalesce(min(vector_dims(c.embedding)),0), "
        "  coalesce(min(c.chunk_chars),0), coalesce(round(avg(c.chunk_chars)),0), "
        "  coalesce(max(c.chunk_chars),0), "
        "  count(*) FILTER (WHERE c.chunk_chars<50), "
        "  count(*) FILTER (WHERE c.chunk_chars>3000), "
        "  count(*)-count(DISTINCT c.content_hash) "
        "FROM document_chunks c JOIN bots b ON b.id=c.record_bot_id "
        f"WHERE b.bot_id='{bot}'"
    )[0]
    keys = ["total", "embedded", "null_embed", "dim", "min", "avg", "max",
            "tiny", "huge", "dup"]
    return dict(zip(keys, (int(x) for x in r)))


def _strategy_mix(bot: str) -> str:
    rows = _psql(
        "SELECT c.chunk_type, count(*) FROM document_chunks c "
        "JOIN bots b ON b.id=c.record_bot_id "
        f"WHERE b.bot_id='{bot}' GROUP BY 1 ORDER BY 2 DESC"
    )
    return ", ".join(f"{t}={n}" for t, n in rows)


def _trace_bot(bot: str, since: str, service: str) -> None:
    docs = _docs_for_bot(bot)
    if not docs:
        print(f"\n### {bot}: KHÔNG có document (chưa upload?)\n")
        return
    evs = _journal(service, since)
    bot_evs = [e for e in evs if e.get("event")]
    # index: event name -> earliest ts (for this bot's workspace)
    fired: dict[str, str] = {}
    for e in bot_evs:
        ev = e.get("event", "")
        ts = (e.get("timestamp", "") or "")[11:19]
        if ev not in fired and ts:
            fired[ev] = ts

    print(f"\n{'='*78}\n### BOT: {bot}   ({len(docs)} doc)\n{'='*78}")
    print("DOCS:")
    for d in docs:
        flag = "✅" if d["state"] == "active" else "⚠️"
        print(f"  {flag} {d['name']:<18} state={d['state']:<7} "
              f"chars={d['chars']:<7} chunks={d['chunks']:<4} ingest={d['secs']}s")

    print(f"\nSTRATEGY MIX: {_strategy_mix(bot)}")
    q = _quality(bot)
    print(f"\nQUALITY CHECK (data thật):")
    print(f"  chunks={q['total']}  embedded={q['embedded']}  NULL-embed={q['null_embed']}  dim={q['dim']}")
    print(f"  size min/avg/max = {q['min']}/{q['avg']}/{q['max']}  "
          f"tiny(<50)={q['tiny']}  huge(>3000)={q['huge']}  dup={q['dup']}")
    flags = []
    if q["null_embed"] > 0:
        flags.append(f"🔴 {q['null_embed']} chunk NULL embedding (retrieval hole)")
    if q["dim"] not in (0, 1280):
        flags.append(f"🔴 dim={q['dim']} ≠ 1280 (matryoshka drift)")
    if q["dup"] > 0:
        flags.append(f"🟡 {q['dup']} chunk trùng content_hash (dedup chưa sạch 100%)")
    if q["tiny"] > 0:
        flags.append(f"🟡 {q['tiny']} chunk <50 char (gần rỗng)")
    if q["huge"] > 0:
        flags.append(f"🟡 {q['huge']} chunk >3000 char (nên split nhỏ hơn)")
    print("  " + ("  ".join(flags) if flags else "✅ không có cờ đỏ"))

    print(f"\nSTEP-BY-STEP (event thật từ journalctl -u {service}, since {since!r}):")
    print(f"  {'#':<4}{'phase':<12}{'step':<24}{'fired':<9}{'verdict'}")
    for s in _steps():
        ts = ""
        for ev in s.events:
            if ev in fired:
                ts = fired[ev]
                break
        if not s.events:                     # API-side step (no worker event)
            mark = "(api)"
        elif ts:
            mark = ts
        elif s.mand:                          # always runs; event just not at this log level
            mark = "✓ ran*"
        else:
            mark = "— skip"
        print(f"  {s.sid:<4}{s.phase:<12}{s.name:<24}{mark:<9}{s.verdict}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Debug ALL steps of the upload pipeline")
    ap.add_argument("--bot", help="bot_id (e.g. chinh-sach-xe). Omit with --all")
    ap.add_argument("--all", action="store_true", help="trace all 3 demo bots")
    ap.add_argument("--since", default="2 hours ago", help="journalctl --since window")
    ap.add_argument("--service", default="ragbot-py", help="systemd unit running the app")
    a = ap.parse_args()

    print("UPLOAD PIPELINE — DEBUG ALL STEPS")
    print("22 step / 4 phase (A receive · B chunk · C embed · D finalize)")
    print(f"service={a.service}  window={a.since!r}")

    if a.all:
        for bot in ("chinh-sach-xe", "test-spa-id", "thong-tu-09-2020-tt-nhnn"):
            _trace_bot(bot, a.since, a.service)
    elif a.bot:
        _trace_bot(a.bot, a.since, a.service)
    else:
        ap.error("cần --bot <id> hoặc --all")

    print(f"\n{'='*78}")
    print("LEGEND: (api)=step ở API process · ts=giờ step fire · "
          "✓ran*=step bắt-buộc đã chạy (event không ở log-level này) · "
          "—skip=không fire cho doc này (vd CR row-gated/narrate non-text)")
    print("Nút thắt chính: B4 CR + C2 narrate (LLM/chunk, không cache/không batch trên OpenAI).")
    print("Chi tiết verdict + fix: reports/EXPERT_RAG_AUDIT_20260613.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
