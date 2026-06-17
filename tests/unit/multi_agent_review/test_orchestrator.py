from __future__ import annotations

import pytest

try:
    from ragbot.application.services.multi_agent_review import (
        AgentResponse,
        AgentRole,
        ArtefactKind,
        MultiAgentReviewOrchestrator,
        ReviewArtefact,
        ReviewVerdict,
        build_default_review_team,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "multi_agent_review subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from tests.unit.multi_agent_review.conftest import FakeLLM, make_reply


@pytest.mark.asyncio
async def test_orchestrator_runs_six_specialists_then_auditor(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(
        replies=[
            make_reply(summary="architect ok", verdict="approved"),
            make_reply(summary="rag ok", verdict="approved"),
            make_reply(summary="vn ok", verdict="approved"),
            make_reply(summary="quality ok", verdict="approved"),
            make_reply(summary="evaluator ok", verdict="approved"),
            make_reply(summary="critic ok", verdict="approved"),
            make_reply(summary="auditor merged", verdict="approved"),
        ]
    )
    specialists, auditor = build_default_review_team(
        llm=fake, specialist_spec=fake_spec
    )
    orch = MultiAgentReviewOrchestrator(specialists, auditor, debate_rounds=0)
    artefact = ReviewArtefact(text="ship the thing", kind=ArtefactKind.PLAN)

    report = await orch.run(
        artefact, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert len(report.rounds) == 1
    assert len(report.rounds[0]) == 6
    auditor_resp = report.auditor
    assert isinstance(auditor_resp, AgentResponse)
    assert auditor_resp.role is AgentRole.AUDITOR
    assert auditor_resp.verdict is ReviewVerdict.APPROVED
    assert auditor_resp.summary == "auditor merged"
    assert report.verdict is ReviewVerdict.APPROVED
    assert report.total_cost_usd == pytest.approx(0.0001 * 7)
    assert report.total_tokens_in == 200 * 7
    assert report.total_tokens_out == 100 * 7
    assert len(fake.calls) == 7


@pytest.mark.asyncio
async def test_orchestrator_runs_debate_round_when_specialists_disagree(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(
        replies=[
            make_reply(summary="r1 architect", issues=["coupling"], verdict="approved_with_fix"),
            make_reply(summary="r1 rag", verdict="approved"),
            make_reply(summary="r1 vn", verdict="approved"),
            make_reply(summary="r1 quality", verdict="approved"),
            make_reply(summary="r1 eval", verdict="approved"),
            make_reply(summary="r1 critic", verdict="approved"),
            make_reply(summary="r2 architect", verdict="approved_with_fix"),
            make_reply(summary="r2 rag", verdict="approved_with_fix"),
            make_reply(summary="r2 vn", verdict="approved_with_fix"),
            make_reply(summary="r2 quality", verdict="approved_with_fix"),
            make_reply(summary="r2 eval", verdict="approved_with_fix"),
            make_reply(summary="r2 critic", verdict="approved_with_fix"),
            make_reply(summary="auditor merged", issues=["coupling"], verdict="approved_with_fix"),
        ]
    )
    specialists, auditor = build_default_review_team(
        llm=fake, specialist_spec=fake_spec
    )
    orch = MultiAgentReviewOrchestrator(specialists, auditor, debate_rounds=1)
    artefact = ReviewArtefact(text="some plan", kind=ArtefactKind.PLAN)

    report = await orch.run(
        artefact, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert len(report.rounds) == 2
    assert report.verdict is ReviewVerdict.APPROVED_WITH_FIX
    round2_user_msgs = [
        c["user"] for c in fake.calls[6:12]
    ]
    assert all("PRIOR ROUND" in m for m in round2_user_msgs)
    assert all("[architect]" in m for m in round2_user_msgs)


@pytest.mark.asyncio
async def test_orchestrator_skips_debate_when_round1_all_approved(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(
        replies=[
            make_reply(summary=f"r1 {i}", verdict="approved") for i in range(6)
        ]
        + [make_reply(summary="auditor", verdict="approved")]
    )
    specialists, auditor = build_default_review_team(
        llm=fake, specialist_spec=fake_spec
    )
    orch = MultiAgentReviewOrchestrator(specialists, auditor, debate_rounds=2)
    artefact = ReviewArtefact(text="clean plan", kind=ArtefactKind.PLAN)

    report = await orch.run(
        artefact, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert len(report.rounds) == 1
    assert len(fake.calls) == 7


def test_orchestrator_rejects_zero_specialists(fake_spec) -> None:
    fake = FakeLLM(replies=[])
    _, auditor = build_default_review_team(llm=fake, specialist_spec=fake_spec)
    with pytest.raises(ValueError, match="at least one specialist"):
        MultiAgentReviewOrchestrator([], auditor)


def test_orchestrator_caps_debate_rounds(fake_spec) -> None:
    fake = FakeLLM(replies=[])
    specialists, auditor = build_default_review_team(
        llm=fake, specialist_spec=fake_spec
    )
    with pytest.raises(ValueError, match="capped"):
        MultiAgentReviewOrchestrator(specialists, auditor, debate_rounds=99)


@pytest.mark.asyncio
async def test_orchestrator_rejected_when_one_specialist_rejects(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(
        replies=[
            make_reply(summary="r1 architect", verdict="approved"),
            make_reply(summary="r1 rag", verdict="approved"),
            make_reply(summary="r1 vn", verdict="approved"),
            make_reply(summary="r1 quality", issues=["sacred breach"], verdict="rejected"),
            make_reply(summary="r1 eval", verdict="approved"),
            make_reply(summary="r1 critic", verdict="approved"),
            make_reply(summary="r2 architect", verdict="approved"),
            make_reply(summary="r2 rag", verdict="approved"),
            make_reply(summary="r2 vn", verdict="approved"),
            make_reply(summary="r2 quality", issues=["sacred breach"], verdict="rejected"),
            make_reply(summary="r2 eval", verdict="approved"),
            make_reply(summary="r2 critic", verdict="approved"),
            make_reply(summary="auditor merged", verdict="approved_with_fix"),
        ]
    )
    specialists, auditor = build_default_review_team(
        llm=fake, specialist_spec=fake_spec
    )
    orch = MultiAgentReviewOrchestrator(specialists, auditor, debate_rounds=1)
    artefact = ReviewArtefact(text="harmless plan", kind=ArtefactKind.PLAN)

    report = await orch.run(
        artefact, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert report.verdict is ReviewVerdict.REJECTED
