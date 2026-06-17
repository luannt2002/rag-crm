#!/usr/bin/env python3
"""Replay a pipeline_audit JSONL into a human-readable per-request report.

Usage::

    python scripts/audit_logger_replay.py reports/pipeline_audit_<bot_id>_<date>.jsonl
    python scripts/audit_logger_replay.py reports/pipeline_audit_*.jsonl --request <uuid>

Groups events by ``request_id`` (query stage) and renders an ordered
bullet trace + per-stage VERDICT (PASS / WARN / FAIL) so a leader can
audit one question end-to-end without grepping raw JSON.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Heuristic verdict — kept simple on purpose. Tune thresholds via constants
# when ops experience proves a stage's "warn" boundary should move.
# ---------------------------------------------------------------------------
_WARN_TOP_SCORE_BYPASS = 0.05  # RRF-shaped score range; below = poor signal
_FAIL_RELEVANT_FRACTION = 0.2  # graded relevant / candidates_count


def _verdict_for_stage(events_by_event: dict[str, dict]) -> dict[str, str]:
    """Return {event_name: 'PASS'|'WARN'|'FAIL'} for the events present."""
    out: dict[str, str] = {}
    if (cache := events_by_event.get("cache_check")) is not None:
        out["cache_check"] = "PASS"  # hit OR miss are both legitimate
    if (intent := events_by_event.get("intent_extracted")) is not None:
        out["intent_extracted"] = (
            "PASS"
            if intent["data"].get("intent") not in ("out_of_scope",)
            else "WARN"
        )
    if (hs := events_by_event.get("hybrid_search_executed")) is not None:
        top = float(hs["data"].get("top_score") or 0)
        if top == 0:
            out["hybrid_search_executed"] = "FAIL"
        elif top < _WARN_TOP_SCORE_BYPASS:
            out["hybrid_search_executed"] = "WARN"
        else:
            out["hybrid_search_executed"] = "PASS"
    if (rr := events_by_event.get("rerank_executed")) is not None:
        out["rerank_executed"] = (
            "WARN"
            if rr["data"].get("mode") in ("disabled", "no_reranker")
            else "PASS"
        )
    if (gr := events_by_event.get("grade_executed")) is not None:
        d = gr["data"]
        rel = int(d.get("relevant", 0))
        total = max(rel + int(d.get("irrelevant", 0)) + int(d.get("ambiguous", 0)), 1)
        if rel == 0 and not d.get("fallback_used"):
            out["grade_executed"] = "FAIL"
        elif (rel / total) < _FAIL_RELEVANT_FRACTION:
            out["grade_executed"] = "WARN"
        else:
            out["grade_executed"] = "PASS"
    if (qc := events_by_event.get("query_completed")) is not None:
        out["query_completed"] = (
            "FAIL" if qc["data"].get("answer_chars", 0) == 0 else "PASS"
        )
    return out


def _summarise_request(request_id: str, events: list[dict]) -> str:
    """Pretty-print one request's audit trail."""
    lines: list[str] = []
    bar = "=" * 70
    lines.append(bar)
    lines.append(f"REQUEST {request_id}")
    lines.append(bar)
    by_event: dict[str, dict] = {}
    for ev in events:
        by_event[ev["event"]] = ev
        data = ev["data"]
        lines.append(f"[{ev['event']}] {_render_data(ev['event'], data)}")
    verdicts = _verdict_for_stage(by_event)
    if verdicts:
        lines.append("")
        lines.append("VERDICT per stage:")
        for ev_name, v in verdicts.items():
            mark = {"PASS": "[ok]", "WARN": "[!!]", "FAIL": "[xx]"}[v]
            lines.append(f"  {mark} {ev_name}: {v}")
    return "\n".join(lines)


def _render_data(event: str, data: dict[str, Any]) -> str:
    """One-liner for the event payload — keeps the output scannable."""
    if event == "query_received":
        return f'q="{(data.get("question") or "")[:120]}"'
    if event == "cache_check":
        return f"hit={data.get('hit')} reason={data.get('reason', '-')}"
    if event == "intent_extracted":
        return (
            f"intent={data.get('intent')} condensed={data.get('condensed')} "
            f"history={data.get('had_history')}"
        )
    if event == "hybrid_search_executed":
        return (
            f"candidates={data.get('candidates_count')} "
            f"top={data.get('top_score')} filter={bool(data.get('metadata_filter'))}"
        )
    if event == "chunks_retrieved":
        return f"n={data.get('count')} (preview suppressed)"
    if event == "rerank_executed":
        return (
            f"mode={data.get('mode')} {data.get('before')}->{data.get('after')} "
            f"top={data.get('top_score_active')}"
        )
    if event == "mmr_dedup":
        return (
            f"{data.get('before')}->{data.get('after')} "
            f"lambda={data.get('lambda')}"
        )
    if event == "grade_executed":
        return (
            f"rel={data.get('relevant')} irr={data.get('irrelevant')} "
            f"amb={data.get('ambiguous')} adequate={data.get('retrieval_adequate')} "
            f"fallback={data.get('fallback_used')}"
        )
    if event == "generate_started":
        return f"chunks={data.get('context_chunks')} chars={data.get('context_chars')}"
    if event == "query_completed":
        return (
            f"type={data.get('answer_type')} chars={data.get('answer_chars')} "
            f"top_score={data.get('top_score')} cost=${data.get('cost_usd')}"
        )
    return json.dumps(data, ensure_ascii=False)[:200]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="JSONL audit file(s)")
    parser.add_argument(
        "--request",
        default=None,
        help="Filter to a single request_id (default: print all)",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Print ingest events (skipped by default — query is the leader focus).",
    )
    args = parser.parse_args(argv)

    by_request: dict[str, list[dict]] = defaultdict(list)
    ingest_events: list[dict] = []
    for path_str in args.paths:
        path = Path(path_str)
        if not path.exists():
            print(f"warn: {path} not found", file=sys.stderr)
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("stage") == "ingest":
                    ingest_events.append(ev)
                else:
                    rid = ev.get("data", {}).get("request_id") or "unknown"
                    by_request[rid].append(ev)

    if args.ingest and ingest_events:
        print("=" * 70)
        print(f"INGEST events: {len(ingest_events)}")
        print("=" * 70)
        for ev in ingest_events[:50]:
            print(f"[{ev['event']}] {json.dumps(ev['data'], ensure_ascii=False)[:200]}")
        if len(ingest_events) > 50:
            print(f"... ({len(ingest_events) - 50} more ingest events truncated)")
        print()

    target = args.request
    for rid, events in by_request.items():
        if target and rid != target:
            continue
        print(_summarise_request(rid, events))
        print()

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
