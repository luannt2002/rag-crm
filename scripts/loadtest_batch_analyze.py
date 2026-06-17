#!/usr/bin/env python3
"""Post-hoc batch analyser for any load-test JSON.

Re-reads an existing aggregate output (the file produced by
`scripts/test_75q_load.py`) and emits a markdown breakdown grouped into
fixed-size batches. Useful for re-analysing R1-R7 historical JSONs at
batch granularity *without* re-running the round.

Usage::

    .venv/bin/python scripts/loadtest_batch_analyze.py \
        --input /tmp/mega_round9_OLD_<ts>.json \
        --batch-size 10 \
        --output /tmp/round9_old_batch_analyze.md

App-mindset (CLAUDE.md): pure tooling — never touches the production
pipeline, never injects text into the LLM, never overrides answers.
Domain-neutral: no brand / industry literals.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Allow `from scripts._loadtest_common ...` when this script is run by path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._loadtest_common import (  # noqa: E402
    DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS,
    DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE,
)


# ---------------------------------------------------------------------------
# Reusing the harness helpers keeps the markdown shape identical to the
# live mode. Loading the harness by file-path matches the existing test
# pattern (scripts/ is not a package member of `ragbot`).
# ---------------------------------------------------------------------------


def _load_harness_helpers() -> Any:
    import importlib.util

    script_path = _REPO_ROOT / "scripts" / "test_75q_load.py"
    spec = importlib.util.spec_from_file_location("_t75q_harness_for_analyze", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load harness at {script_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _turn_dict_to_dataclass(harness: Any, t: dict[str, Any]) -> Any:
    """Coerce a JSON-loaded turn dict back into a TurnResult dataclass.

    The harness summariser expects dataclass attributes; the JSON stores
    the same keys. Missing optional fields are tolerated by defaulting.
    """
    fields = harness.TurnResult.__dataclass_fields__
    kwargs: dict[str, Any] = {}
    for name, fld in fields.items():
        if name in t:
            kwargs[name] = t[name]
        else:
            # dataclass defaults handle the rest
            continue
    return harness.TurnResult(**kwargs)


def analyze(input_path: Path, *, batch_size: int) -> tuple[str, list[dict[str, Any]]]:
    """Return (markdown_report, list_of_per_batch_summary_dicts)."""
    if batch_size <= 0:
        raise ValueError("--batch-size must be > 0")
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    turns_json = raw.get("turns") or []
    if not turns_json:
        raise ValueError(f"no turns in {input_path}")

    harness = _load_harness_helpers()
    turns = [_turn_dict_to_dataclass(harness, t) for t in turns_json]
    batches = harness.slice_into_batches(turns, batch_size=batch_size)
    total = len(batches)

    parts: list[str] = []
    parts.append(
        f"# Batch analyse — {input_path.name} ({total} batches × {batch_size})"
    )
    parts.append("")
    config = raw.get("config") or {}
    if config:
        parts.append("## Source config")
        parts.append("")
        parts.append(f"- bot_id: `{config.get('bot_id', '')}`")
        parts.append(f"- tenant_id: `{config.get('tenant_id', '')}`")
        parts.append(f"- channel_type: `{config.get('channel_type', '')}`")
        parts.append(f"- rooms: `{config.get('rooms', '')}`")
        parts.append("")

    summaries: list[dict[str, Any]] = []
    for bidx, (lo, hi, sub) in enumerate(batches, start=1):
        s = harness.summarize_batch(
            sub,
            top_n_worst_refuse=DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE,
            preview_chars=DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS,
        )
        summaries.append({"idx": bidx, "turn_range": [lo, hi], "summary": s})
        parts.append(
            harness.format_batch_markdown(
                batch_idx=bidx,
                total_batches=total,
                turn_range=(lo, hi),
                summary=s,
            )
        )
    return "\n".join(parts), summaries


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Post-hoc batch analyser for load-test JSON output."
    )
    p.add_argument("--input", required=True, help="Path to aggregate <output>.json")
    p.add_argument(
        "--batch-size",
        type=int,
        required=True,
        help="Batch size (must be > 0)",
    )
    p.add_argument(
        "--output",
        default="",
        help=(
            "Path for the markdown report. If empty, prints to stdout."
        ),
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"ERROR: input not found: {in_path}", file=sys.stderr)
        return 2
    md, _summaries = analyze(in_path, batch_size=int(args.batch_size))
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Wrote {out_path}")
    else:
        sys.stdout.write(md)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
