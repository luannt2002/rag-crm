"""Aggregate Locust CSV outputs into a single markdown report.

Reads every ``*_stats.csv`` and ``*_failures.csv`` in the given directory
and produces a markdown report with latency percentiles, RPS, error rate,
and a verdict against world-class targets.

Usage:
    python3 scripts/load_test/parse_results.py reports/load_test
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from pathlib import Path
from typing import Any

# World-class baseline targets (used only for verdict labels).
TARGET_P50_MS = 2000
TARGET_P95_MS = 5000
TARGET_P99_MS = 10_000
TARGET_TTFT_P50_MS = 500
TARGET_ERROR_RATE = 0.001  # 0.1%
TARGET_MIN_RPS = 5.0


def _verdict(value: float, target: float, *, lower_is_better: bool = True) -> str:
    if lower_is_better:
        if value <= target:
            return "PASS"
        if value <= target * 2:
            return "WARN"
        return "FAIL"
    if value >= target:
        return "PASS"
    if value >= target * 0.5:
        return "WARN"
    return "FAIL"


def _parse_stats_csv(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _read_failures(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        total = 0
        for row in reader:
            try:
                total += int(row.get("Occurrences", "0") or "0")
            except ValueError:
                continue
        return total


def _scenario_from_prefix(prefix: str) -> str:
    parts = prefix.split("_")
    return parts[0] if parts else prefix


def _format_row(label: str, value: str, target: str, verdict: str) -> str:
    return f"| {label} | {value} | {target} | {verdict} |"


def _aggregate_dir(reports_dir: Path) -> str:
    stats_files = sorted(reports_dir.glob("*_stats.csv"))
    if not stats_files:
        return f"# Load test results\n\nNo stats csv found under `{reports_dir}`.\n"

    sections: list[str] = []
    sections.append("# Load Test Results")
    sections.append("")
    sections.append(f"- Generated: {dt.datetime.now().isoformat(timespec='seconds')}")
    sections.append(f"- Source dir: `{reports_dir}`")
    sections.append("")
    sections.append("## World-class targets")
    sections.append("")
    sections.append(f"- P50 < {TARGET_P50_MS} ms")
    sections.append(f"- P95 < {TARGET_P95_MS} ms")
    sections.append(f"- P99 < {TARGET_P99_MS} ms")
    sections.append(f"- TTFT P50 < {TARGET_TTFT_P50_MS} ms (streaming)")
    sections.append(f"- Error rate < {TARGET_ERROR_RATE * 100:.2f}%")
    sections.append(f"- Sustained rps >= {TARGET_MIN_RPS}")
    sections.append("")

    for stats_path in stats_files:
        prefix = stats_path.name.removesuffix("_stats.csv")
        scenario = _scenario_from_prefix(prefix)
        failures_path = reports_dir / f"{prefix}_failures.csv"
        rows = _parse_stats_csv(stats_path)
        agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
        endpoint_rows = [r for r in rows if r.get("Name") not in ("", "Aggregated")]
        total_failures = _read_failures(failures_path)

        sections.append(f"## Scenario: `{scenario}`  ({prefix})")
        sections.append("")
        if not agg:
            sections.append("_no aggregated row in csv (run produced zero requests?)_")
            sections.append("")
            continue

        try:
            req_count = int(agg.get("Request Count", "0") or 0)
            fail_count = int(agg.get("Failure Count", "0") or 0)
            rps = float(agg.get("Requests/s", "0") or 0)
            p50 = float(agg.get("50%", "0") or 0)
            p95 = float(agg.get("95%", "0") or 0)
            p99 = float(agg.get("99%", "0") or 0)
            avg = float(agg.get("Average Response Time", "0") or 0)
            mx = float(agg.get("Max Response Time", "0") or 0)
        except (ValueError, TypeError):
            sections.append(f"_failed to parse aggregate row: {agg}_")
            sections.append("")
            continue

        err_rate = (fail_count / req_count) if req_count else 0.0

        sections.append("### Throughput")
        sections.append("")
        sections.append("| Metric | Value | Target | Verdict |")
        sections.append("| --- | --- | --- | --- |")
        sections.append(_format_row("Requests", str(req_count), "-", "-"))
        sections.append(_format_row("RPS", f"{rps:.2f}", f">= {TARGET_MIN_RPS}", _verdict(rps, TARGET_MIN_RPS, lower_is_better=False)))
        sections.append(_format_row("Failures", f"{fail_count} ({err_rate * 100:.2f}%)", f"< {TARGET_ERROR_RATE * 100:.2f}%", _verdict(err_rate, TARGET_ERROR_RATE)))
        sections.append(_format_row("Failures(file)", str(total_failures), "-", "-"))
        sections.append("")

        sections.append("### Latency (ms)")
        sections.append("")
        sections.append("| Metric | Value | Target | Verdict |")
        sections.append("| --- | --- | --- | --- |")
        sections.append(_format_row("Avg", f"{avg:.0f}", "-", "-"))
        sections.append(_format_row("P50", f"{p50:.0f}", f"< {TARGET_P50_MS}", _verdict(p50, TARGET_P50_MS)))
        sections.append(_format_row("P95", f"{p95:.0f}", f"< {TARGET_P95_MS}", _verdict(p95, TARGET_P95_MS)))
        sections.append(_format_row("P99", f"{p99:.0f}", f"< {TARGET_P99_MS}", _verdict(p99, TARGET_P99_MS)))
        sections.append(_format_row("Max", f"{mx:.0f}", "-", "-"))
        sections.append("")

        if endpoint_rows:
            sections.append("### Per-endpoint breakdown")
            sections.append("")
            sections.append("| Endpoint | Type | N | Fails | RPS | Avg | P50 | P95 | P99 | Max |")
            sections.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
            for r in endpoint_rows:
                try:
                    sections.append(
                        f"| {r.get('Name','')} | {r.get('Type','')} | "
                        f"{int(r.get('Request Count','0') or 0)} | "
                        f"{int(r.get('Failure Count','0') or 0)} | "
                        f"{float(r.get('Requests/s','0') or 0):.2f} | "
                        f"{float(r.get('Average Response Time','0') or 0):.0f} | "
                        f"{float(r.get('50%','0') or 0):.0f} | "
                        f"{float(r.get('95%','0') or 0):.0f} | "
                        f"{float(r.get('99%','0') or 0):.0f} | "
                        f"{float(r.get('Max Response Time','0') or 0):.0f} |"
                    )
                except (ValueError, TypeError):
                    continue
            sections.append("")

    sections.append("## Notes")
    sections.append("")
    sections.append("- DB pool / Redis ops snapshots are captured separately by `run_load_test.sh` if the helper is enabled.")
    sections.append("- Latency targets are illustrative baselines, not contractual SLOs.")
    sections.append("")
    return "\n".join(sections)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: parse_results.py <reports_dir> [output_md]", file=sys.stderr)
        return 2
    reports_dir = Path(sys.argv[1]).resolve()
    if not reports_dir.is_dir():
        print(f"not a dir: {reports_dir}", file=sys.stderr)
        return 2
    output = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) >= 3
        else reports_dir.parent / f"LOAD_TEST_RESULTS_{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    md = _aggregate_dir(reports_dir)
    output.write_text(md, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
