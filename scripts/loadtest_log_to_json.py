"""Reconstruct a load-test JSON aggregate from a live harness stdout log.

Why: ``scripts/test_75q_load.py`` writes its JSON aggregate ONLY at end of
``main_async``. If the harness crashes after the last turn but before the
write (R9 OLD regression — see ``/tmp/r9_old.log``), all 75 turns of work
are observable in the log but absent from disk.

This module parses the per-turn line emitted by the harness::

    [r<room>] Q<idx>/<total> <CLASSIFICATION>  chunks=<n> top=<x> dur=<x>ms cost=$<x>  <question>

…and rebuilds a JSON file shaped exactly like the harness's own
``main_async`` output (``config``, ``summary``, ``turns``) so downstream
analysers do not need to special-case "live-log-derived" runs.

Limitations: live log lacks ``answer``, ``citations``, ``sources``,
``chunks``, ``request_id``, ``tokens_in``, ``tokens_out``,
``cached_tokens``. Reconstructed turns set those to default empties; the
``summary`` block recomputes ``counts``, ``rates_pct``, ``cost_usd_*``,
and ``latency_ms_p50/p95/p99/max`` from what the log DOES carry.

Per CLAUDE.md: domain-neutral, zero-hardcode (constants imported from
``shared/constants.py`` where possible; the regex patterns are
test-tooling parsers and live with the harness scripts), narrow except.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Live-log line shape — pinned by ``scripts/test_75q_load.py`` formatter.
# Captures: room, idx, total, classification, chunks, top, dur, cost, question.
TURN_LINE_RE = re.compile(
    r"^\s*\[r(?P<room>\d+)\]\s+"
    r"Q(?P<idx>\d+)/(?P<total>\d+)\s+"
    r"(?P<cls>\S+)\s+"
    r"chunks=(?P<chunks>\d+)\s+"
    r"top=(?P<top>[\d.]+)\s+"
    r"dur=(?P<dur>\d+)ms\s+"
    r"cost=\$(?P<cost>[\d.]+)\s+"
    r"(?P<q>.+?)\s*$"
)

# First "Bot: ..." header gives identity. Tolerant of multiple spaces.
HEADER_RE = re.compile(
    r"^Bot:\s+(?P<bot_id>\S+)\s+tenant=(?P<tenant_id>\d+)\s+"
    r"channel=(?P<channel>\S+)\s+\|\s+rooms=\[(?P<rooms>[\d,\s]+)\]\s+"
    r"\|\s+bypass_cache=(?P<bypass>True|False)\s+debug=(?P<debug>\S+)"
)


@dataclass
class _ReconstructedTurn:
    """Same shape as ``test_75q_load.TurnResult`` for the fields parseable
    from a live log. Missing fields default to safe empties so downstream
    analysers can union-merge."""

    room: int
    idx: int
    question: str
    classification: str
    chunks_used: int
    top_score: float
    duration_ms: int
    cost_usd: float
    answer: str = ""
    answer_type: str | None = None
    answer_reason: str | None = None
    top_score_min: float = 0.0
    history_msgs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    wall_ms: float = 0.0
    citations: list[Any] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    chunks: list[Any] = field(default_factory=list)
    request_id: str | None = None
    error: str | None = None
    is_repeat_probe: bool = False


def parse_log(log_text: str) -> tuple[dict[str, Any], list[_ReconstructedTurn]]:
    """Parse a harness stdout log into a config dict + list of turns.

    Tolerant: skips non-matching lines (room headers, blank lines,
    tracebacks). Returns whatever could be parsed; caller validates count.
    """
    config: dict[str, Any] = {}
    turns: list[_ReconstructedTurn] = []
    for line in log_text.splitlines():
        if not config:
            m_hdr = HEADER_RE.match(line)
            if m_hdr is not None:
                config = {
                    "bot_id": m_hdr["bot_id"],
                    "tenant_id": int(m_hdr["tenant_id"]),
                    "channel_type": m_hdr["channel"],
                    "rooms": [int(x.strip()) for x in m_hdr["rooms"].split(",")],
                    "bypass_cache": m_hdr["bypass"] == "True",
                    "debug": m_hdr["debug"],
                    "questions_file": "<reconstructed-from-log>",
                    "batch_size": 0,
                }
                continue
        m_turn = TURN_LINE_RE.match(line)
        if m_turn is None:
            continue
        turns.append(
            _ReconstructedTurn(
                room=int(m_turn["room"]),
                idx=int(m_turn["idx"]),
                question=m_turn["q"],
                classification=m_turn["cls"],
                chunks_used=int(m_turn["chunks"]),
                top_score=float(m_turn["top"]),
                duration_ms=int(m_turn["dur"]),
                cost_usd=float(m_turn["cost"]),
            )
        )
    return config, turns


def summarize(turns: list[_ReconstructedTurn]) -> dict[str, Any]:
    """Recompute the aggregate summary block from per-turn data."""
    total = len(turns)
    counts: dict[str, int] = {}
    for t in turns:
        counts[t.classification] = counts.get(t.classification, 0) + 1
    rates = (
        {k: (v / total * 100.0) for k, v in counts.items()} if total > 0 else {}
    )
    durations = [t.duration_ms for t in turns if t.duration_ms > 0]
    costs = [t.cost_usd for t in turns]

    def _pct(vals: list[int], q: float) -> int:
        if not vals:
            return 0
        s = sorted(vals)
        # Nearest-rank percentile (matches the live harness).
        k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
        return int(s[k])

    return {
        "total_turns": total,
        "counts": counts,
        "rates_pct": rates,
        "cost_usd_total": round(sum(costs), 5),
        "cost_usd_per_turn_avg": round(
            (sum(costs) / total) if total > 0 else 0.0, 7
        ),
        "latency_ms_p50": _pct(durations, 0.50),
        "latency_ms_p95": _pct(durations, 0.95),
        "latency_ms_p99": _pct(durations, 0.99),
        "latency_ms_max": int(max(durations)) if durations else 0,
        "duration_zero_count": sum(1 for d in durations if d == 0),
    }


def reconstruct(log_path: Path, output_path: Path) -> dict[str, Any]:
    """Read a log file, write a JSON aggregate, return the payload."""
    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    config, turns = parse_log(log_text)
    summary = summarize(turns)
    payload = {
        "config": config,
        "summary": summary,
        "turns": [asdict(t) for t in turns],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Reconstruct load-test JSON aggregate from a live stdout log. "
            "Use when the harness crashed after all turns completed but "
            "before main_async reached its JSON-write block."
        )
    )
    p.add_argument("--log", required=True, help="Path to live harness log.")
    p.add_argument("--output", required=True, help="Path to write JSON.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = reconstruct(Path(args.log), Path(args.output))
    summary = payload["summary"]
    print(
        f"reconstructed {summary['total_turns']} turns -> {args.output}",
        flush=True,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
