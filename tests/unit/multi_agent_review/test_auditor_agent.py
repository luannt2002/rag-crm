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
    from ragbot.application.services.multi_agent_review.agents.auditor_agent import (
        AuditorAgent,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "multi_agent_review subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from tests.unit.multi_agent_review.conftest import FakeLLM, make_reply


def _resp(
    role: AgentRole,
    *,
    verdict: ReviewVerdict,
    issues: list[str] | None = None,
) -> AgentResponse:
    return AgentResponse(
        role=role,
        summary=f"{role.value} ok",
        issues=issues or [],
        verdict=verdict,
    )


@pytest.mark.asyncio
async def test_auditor_returns_rejected_when_specialist_rejected(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="merged", verdict="approved")])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    responses = [
        _resp(AgentRole.ARCHITECT, verdict=ReviewVerdict.APPROVED),
        _resp(
            AgentRole.QUALITY_GUARDIAN,
            verdict=ReviewVerdict.REJECTED,
            issues=["unsafe pattern"],
        ),
    ]
    artefact = ReviewArtefact(text="harmless plan", kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact, responses, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert out.verdict is ReviewVerdict.REJECTED


@pytest.mark.asyncio
async def test_auditor_short_circuits_on_sacred_keyword_in_artefact(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    artefact = ReviewArtefact(
        text="rename column embedding_v3 to embedding",
        kind=ArtefactKind.CODE_DIFF,
    )
    responses = [_resp(AgentRole.ARCHITECT, verdict=ReviewVerdict.APPROVED)]

    out = await auditor.synthesise(
        artefact, responses, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert out.verdict is ReviewVerdict.REJECTED
    assert any("version-ref" in i for i in out.issues)
    assert len(fake.calls) == 0


@pytest.mark.asyncio
async def test_auditor_short_circuits_when_specialist_flags_hallu(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    responses = [
        _resp(
            AgentRole.QUALITY_GUARDIAN,
            verdict=ReviewVerdict.APPROVED_WITH_FIX,
            issues=["risk of hallu fabrication on price line"],
        )
    ]
    artefact = ReviewArtefact(text="clean plan body", kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact, responses, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert out.verdict is ReviewVerdict.REJECTED
    assert len(fake.calls) == 0


@pytest.mark.asyncio
async def test_auditor_promotes_with_fix_when_issues_present(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(
        replies=[
            make_reply(
                summary="merged",
                issues=["minor wording"],
                verdict="approved",
            )
        ]
    )
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    responses = [
        _resp(
            AgentRole.ARCHITECT,
            verdict=ReviewVerdict.APPROVED_WITH_FIX,
            issues=["minor wording"],
        )
    ]
    artefact = ReviewArtefact(text="harmless plan", kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact, responses, record_tenant_id=tenant_id, trace_id=trace_id
    )

    assert out.verdict is ReviewVerdict.APPROVED_WITH_FIX
