"""Unit coverage for ``scripts/loadtest_log_to_json.py``.

The harness writes its JSON aggregate ONLY at end of run; if it crashes
between the last turn and the write (R9 OLD regression — see
``/tmp/r9_old.log``), the per-turn data lives only in the log. This
parser rebuilds the JSON from that log — pin the contract so future
log-format drift surfaces in CI.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _load_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "loadtest_log_to_json.py"
    spec = importlib.util.spec_from_file_location("_lt_log_to_json", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# Sample 4-turn live log — header + 3 PASS + 1 REFUSE_NO_DOCS — generic
# question text, NO brand/tenant literals (CLAUDE.md domain-neutral).
_SAMPLE_LOG = """\
Bot: 1234567890 tenant=99 channel=web | rooms=[1, 2, 3, 4, 5] | bypass_cache=True debug=full
Token acquired. Starting 5 room(s) SERIAL.

=== Room 1 — 15 questions ===
  [r01] Q01/15 PASS               chunks=1 top=0.017 dur=11942ms cost=$0.00153  câu hỏi giả lập một
  [r01] Q02/15 PASS               chunks=0 top=0.000 dur=8000ms cost=$0.00080  câu hỏi giả lập hai
  [r01] Q03/15 PASS               chunks=1 top=0.017 dur=10000ms cost=$0.00100  câu hỏi giả lập ba
=== Room 2 — 15 questions ===
  [r02] Q04/15 REFUSE_NO_DOCS     chunks=0 top=0.000 dur=5000ms cost=$0.00050  câu hỏi giả lập bốn
"""


def test_parse_log_extracts_header_and_4_turns() -> None:
    mod = _load_module()
    config, turns = mod.parse_log(_SAMPLE_LOG)

    assert config["bot_id"] == "1234567890"
    assert config["tenant_id"] == 99
    assert config["channel_type"] == "web"
    assert config["rooms"] == [1, 2, 3, 4, 5]
    assert config["bypass_cache"] is True
    assert config["debug"] == "full"
    assert config["batch_size"] == 0  # default for reconstructed runs

    assert len(turns) == 4
    assert turns[0].classification == "PASS"
    assert turns[0].chunks_used == 1
    assert turns[0].top_score == 0.017
    assert turns[0].duration_ms == 11942
    assert turns[0].cost_usd == 0.00153
    assert turns[0].question == "câu hỏi giả lập một"
    assert turns[3].classification == "REFUSE_NO_DOCS"
    assert turns[3].room == 2


def test_summarize_computes_counts_rates_latency_cost() -> None:
    mod = _load_module()
    _config, turns = mod.parse_log(_SAMPLE_LOG)
    summary = mod.summarize(turns)

    assert summary["total_turns"] == 4
    assert summary["counts"] == {"PASS": 3, "REFUSE_NO_DOCS": 1}
    # PASS rate = 3/4 = 75%
    assert summary["rates_pct"]["PASS"] == 75.0
    assert summary["rates_pct"]["REFUSE_NO_DOCS"] == 25.0
    # Cost = 0.00153 + 0.00080 + 0.00100 + 0.00050 = 0.00383
    assert abs(summary["cost_usd_total"] - 0.00383) < 1e-6
    # Per-turn avg
    assert abs(summary["cost_usd_per_turn_avg"] - 0.0009575) < 1e-6
    # Latency max = 11942
    assert summary["latency_ms_max"] == 11942
    # No zero-duration turns in this sample
    assert summary["duration_zero_count"] == 0


def test_reconstruct_writes_valid_json(tmp_path: Path) -> None:
    mod = _load_module()
    log_path = tmp_path / "live.log"
    out_path = tmp_path / "out.json"
    log_path.write_text(_SAMPLE_LOG, encoding="utf-8")
    payload = mod.reconstruct(log_path, out_path)

    # File on disk parses back into matching payload.
    on_disk = json.loads(out_path.read_text(encoding="utf-8"))
    assert on_disk["config"] == payload["config"]
    assert on_disk["summary"]["total_turns"] == 4
    assert len(on_disk["turns"]) == 4

    # Reconstructed turns include the safe-default empty fields.
    t0 = on_disk["turns"][0]
    assert t0["answer"] == ""
    assert t0["citations"] == []
    assert t0["request_id"] is None


def test_parse_log_skips_non_matching_lines() -> None:
    mod = _load_module()
    log_with_traceback = _SAMPLE_LOG + (
        "\nTraceback (most recent call last):\n"
        '  File "/var/www/html/ragbot/scripts/test_75q_load.py", line 684, in main_async\n'
        '    "batch_size": int(args.batch_size or 0),\n'
        "AttributeError: 'Namespace' object has no attribute 'batch_size'\n"
    )
    config, turns = mod.parse_log(log_with_traceback)
    # Traceback adds zero turns, header still parses.
    assert config["bot_id"] == "1234567890"
    assert len(turns) == 4


def test_real_r9_old_log_reconstructs_75_turns() -> None:
    """Smoke check against the actual R9 OLD crash log that motivated
    this script. If the file is missing in the test environment (CI
    container, fresh checkout), skip gracefully — the unit assertions
    above already pin the contract.
    """
    real_log = Path("/tmp/r9_old.log")
    if not real_log.exists():
        return  # Smoke test only — silently skip when the artifact is absent.
    mod = _load_module()
    _config, turns = mod.parse_log(real_log.read_text(encoding="utf-8"))
    # The R9 OLD crash captured exactly 75 turns before AttributeError.
    assert len(turns) == 75
    # Pinned classification distribution from the live log:
    # 28 PASS, 3 REFUSE_WITH_DOCS, 44 REFUSE_NO_DOCS.
    counts: dict[str, int] = {}
    for t in turns:
        counts[t.classification] = counts.get(t.classification, 0) + 1
    assert counts.get("PASS", 0) == 28
    assert counts.get("REFUSE_NO_DOCS", 0) == 44
    assert counts.get("REFUSE_WITH_DOCS", 0) == 3
