"""Multi-agent review CLI.

Run a 7-agent review (6 specialists + Auditor) over an artefact (plan,
sysprompt, code diff) and print a structured report.

Usage:

    python -m scripts.multi_agent_review \\
        --artefact plans/260508-master-replan-tiered/plan_v2.md \\
        --kind plan \\
        --model openai/gpt-4.1-mini \\
        --debate-rounds 1

The CLI does NOT touch the production DB — it builds a minimal LLMSpec
in-memory and wires DirectLiteLLMAdapter. Set OPENAI_API_KEY (or whatever
the model needs) in the shell before running.

The CLI is dev-side tooling. It does NOT run on the chat hot path; the
production query graph keeps using DynamicLiteLLMRouter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.services.multi_agent_review import (
    ArtefactKind,
    MultiAgentReviewOrchestrator,
    ReviewArtefact,
    build_default_review_team,
)
from ragbot.application.services.multi_agent_review.litellm_adapter import (
    DirectLiteLLMAdapter,
)
from ragbot.shared.constants import (
    DEFAULT_MULTI_AGENT_DEBATE_ROUNDS,
    DEFAULT_MULTI_AGENT_MAX_DEBATE_ROUNDS,
    DEFAULT_MULTI_AGENT_MAX_TOKENS,
    DEFAULT_MULTI_AGENT_TEMPERATURE,
)
from ragbot.shared.types import TenantId, TraceId


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="multi_agent_review",
        description="7-agent review of a plan / sysprompt / diff",
    )
    p.add_argument(
        "--artefact",
        type=Path,
        required=True,
        help="path to the artefact file under review",
    )
    p.add_argument(
        "--kind",
        choices=[k.value for k in ArtefactKind],
        default=ArtefactKind.GENERIC.value,
    )
    p.add_argument(
        "--title",
        default="",
        help="optional title (defaults to file stem)",
    )
    p.add_argument(
        "--model",
        default="openai/gpt-4.1-mini",
        help="LiteLLM-prefixed model name for all 7 agents",
    )
    p.add_argument(
        "--provider",
        default="openai",
        help="provider tag (informational; LiteLLM derives from model)",
    )
    p.add_argument(
        "--temperature",
        type=float,
        default=DEFAULT_MULTI_AGENT_TEMPERATURE,
    )
    p.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MULTI_AGENT_MAX_TOKENS,
    )
    p.add_argument(
        "--debate-rounds",
        type=int,
        default=DEFAULT_MULTI_AGENT_DEBATE_ROUNDS,
        help=f"0..{DEFAULT_MULTI_AGENT_MAX_DEBATE_ROUNDS} (orchestrator caps)",
    )
    p.add_argument(
        "--metadata",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="extra context for the artefact; may repeat",
    )
    p.add_argument(
        "--output",
        type=Path,
        help="write JSON report here (else print summary to stdout)",
    )
    return p.parse_args()


def _load_artefact(args: argparse.Namespace) -> ReviewArtefact:
    if not args.artefact.exists():
        sys.exit(f"artefact not found: {args.artefact}")
    text = args.artefact.read_text(encoding="utf-8")
    metadata = {}
    for kv in args.metadata:
        if "=" not in kv:
            sys.exit(f"--metadata expects KEY=VAL, got: {kv!r}")
        k, _, v = kv.partition("=")
        metadata[k.strip()] = v.strip()
    return ReviewArtefact(
        text=text,
        kind=ArtefactKind(args.kind),
        title=args.title or args.artefact.stem,
        metadata=metadata,
    )


def _build_spec(args: argparse.Namespace) -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000000000"),
        model_name=args.model,
        provider=args.provider,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        top_p=1.0,
    )


def _print_report(report) -> None:
    print()
    print("=" * 78)
    print(f"ARTEFACT: {report.artefact.title}  ({report.artefact.kind})")
    print(f"VERDICT:  {report.verdict.upper()}")
    print(f"COST:     ${report.total_cost_usd:.6f}")
    print(f"TOKENS:   in={report.total_tokens_in}  out={report.total_tokens_out}")
    print("=" * 78)
    for round_idx, round_responses in enumerate(report.rounds):
        print(f"\n--- ROUND {round_idx + 1} ---")
        for r in round_responses:
            print(f"\n[{r.role}] verdict={r.verdict}")
            print(f"  summary: {r.summary}")
            if r.issues:
                print("  issues:")
                for i in r.issues:
                    print(f"    - {i}")
            if r.suggestions:
                print("  suggestions:")
                for s in r.suggestions:
                    print(f"    - {s}")
            if r.risks:
                print("  risks:")
                for x in r.risks:
                    print(f"    - {x}")
    if report.auditor is not None:
        a = report.auditor
        print("\n--- AUDITOR ---")
        print(f"verdict: {a.verdict}")
        print(f"summary: {a.summary}")
        if a.issues:
            print("issues:")
            for i in a.issues:
                print(f"  - {i}")
        if a.suggestions:
            print("suggestions:")
            for s in a.suggestions:
                print(f"  - {s}")
        if a.risks:
            print("risks:")
            for x in a.risks:
                print(f"  - {x}")
    print()


def _report_to_dict(report) -> dict:
    return {
        "artefact": {
            "title": report.artefact.title,
            "kind": str(report.artefact.kind),
        },
        "verdict": str(report.verdict),
        "total_cost_usd": report.total_cost_usd,
        "total_tokens_in": report.total_tokens_in,
        "total_tokens_out": report.total_tokens_out,
        "rounds": [
            [
                {
                    "role": str(r.role),
                    "verdict": str(r.verdict),
                    "summary": r.summary,
                    "issues": r.issues,
                    "suggestions": r.suggestions,
                    "risks": r.risks,
                    "cost_usd": r.cost_usd,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                }
                for r in rnd
            ]
            for rnd in report.rounds
        ],
        "auditor": (
            {
                "verdict": str(report.auditor.verdict),
                "summary": report.auditor.summary,
                "issues": report.auditor.issues,
                "suggestions": report.auditor.suggestions,
                "risks": report.auditor.risks,
                "cost_usd": report.auditor.cost_usd,
                "tokens_in": report.auditor.tokens_in,
                "tokens_out": report.auditor.tokens_out,
            }
            if report.auditor is not None
            else None
        ),
    }


async def _run() -> int:
    args = _parse_args()
    artefact = _load_artefact(args)
    spec = _build_spec(args)
    llm = DirectLiteLLMAdapter()

    specialists, auditor = build_default_review_team(
        llm=llm,
        specialist_spec=spec,
    )
    orch = MultiAgentReviewOrchestrator(
        specialists,
        auditor,
        debate_rounds=args.debate_rounds,
    )

    report = await orch.run(
        artefact,
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId(f"cli-review-{uuid4().hex[:8]}"),
    )

    _print_report(report)
    if args.output:
        args.output.write_text(
            json.dumps(_report_to_dict(report), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"JSON report written: {args.output}")

    if str(report.verdict) == "rejected":
        return 2
    return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
