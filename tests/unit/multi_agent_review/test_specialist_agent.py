from __future__ import annotations

import pytest

try:
    from ragbot.application.services.multi_agent_review.agent_port import (
        AgentResponse,
        AgentRole,
        ArtefactKind,
        ReviewArtefact,
        ReviewVerdict,
    )
    from ragbot.application.services.multi_agent_review.agents.specialist_agent import (
        SpecialistAgent,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "multi_agent_review subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from tests.unit.multi_agent_review.conftest import FakeLLM, make_reply


@pytest.mark.asyncio
async def test_specialist_emits_role_and_includes_artefact_in_prompt(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="ok", verdict="approved")])
    agent = SpecialistAgent(AgentRole.ARCHITECT, llm=fake, spec=fake_spec)
    artefact = ReviewArtefact(
        text="Plan: ship T1.S1b non-superuser DSN.",
        kind=ArtefactKind.PLAN,
        title="T1.S1b",
    )

    out = await agent.review(
        artefact, prior=[], record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert out.role is AgentRole.ARCHITECT
    assert out.verdict is ReviewVerdict.APPROVED
    assert len(fake.calls) == 1
    user_msg = fake.calls[0]["user"]
    assert "T1.S1b" in user_msg
    assert "ship T1.S1b non-superuser DSN" in user_msg
    system_msg = fake.calls[0]["system"]
    assert "architect" in system_msg.lower()
    assert "HALLU=0" in system_msg


@pytest.mark.asyncio
async def test_specialist_includes_prior_round_in_debate(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="rebuttal", verdict="approved_with_fix")])
    agent = SpecialistAgent(AgentRole.CRITIC, llm=fake, spec=fake_spec)
    prior = [
        AgentResponse(
            role=AgentRole.ARCHITECT,
            summary="initial",
            issues=["coupling"],
            verdict=ReviewVerdict.APPROVED_WITH_FIX,
        )
    ]
    artefact = ReviewArtefact(text="…", kind=ArtefactKind.PLAN)

    await agent.review(
        artefact, prior=prior, record_tenant_id=tenant_id, trace_id=trace_id
    )

    user_msg = fake.calls[0]["user"]
    assert "PRIOR ROUND" in user_msg
    assert "[architect]" in user_msg
    assert "coupling" in user_msg


def test_specialist_rejects_auditor_role(fake_spec) -> None:
    fake = FakeLLM(replies=[])
    with pytest.raises(ValueError, match="AUDITOR"):
        SpecialistAgent(AgentRole.AUDITOR, llm=fake, spec=fake_spec)
