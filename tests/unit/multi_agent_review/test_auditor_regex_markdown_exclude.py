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


def _approved(role: AgentRole) -> AgentResponse:
    return AgentResponse(
        role=role,
        summary=f"{role.value} ok",
        verdict=ReviewVerdict.APPROVED,
    )


@pytest.mark.asyncio
async def test_fenced_code_block_with_legacy_token_does_not_trigger_violation(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="merged", verdict="approved")])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    body = (
        "Plan describes how the rule rejects forbidden tokens.\n"
        "```python\n"
        "# example of a banned name we want to detect: `_legacy`\n"
        "BAD_NAME = '_legacy'\n"
        "```\n"
        "End of explanation.\n"
    )
    artefact = ReviewArtefact(text=body, kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact,
        [_approved(AgentRole.ARCHITECT)],
        record_tenant_id=tenant_id,
        trace_id=trace_id,
    )

    assert out.verdict is ReviewVerdict.APPROVED
    assert all("version-ref" not in i for i in out.issues)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_inline_backtick_version_ref_does_not_trigger_violation(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="merged", verdict="approved")])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    body = "Avoid suffixes like `_v3` or `_v10` in column names per the rule."
    artefact = ReviewArtefact(text=body, kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact,
        [_approved(AgentRole.ARCHITECT)],
        record_tenant_id=tenant_id,
        trace_id=trace_id,
    )

    assert out.verdict is ReviewVerdict.APPROVED
    assert all("version-ref" not in i for i in out.issues)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_blockquote_warning_about_legacy_does_not_trigger_violation(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="merged", verdict="approved")])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    body = (
        "Reviewer note follows.\n"
        "> never use _legacy in the codebase\n"
        "Continuing with the actual plan content.\n"
    )
    artefact = ReviewArtefact(text=body, kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact,
        [_approved(AgentRole.ARCHITECT)],
        record_tenant_id=tenant_id,
        trace_id=trace_id,
    )

    assert out.verdict is ReviewVerdict.APPROVED
    assert all("version-ref" not in i for i in out.issues)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_plain_prose_legacy_token_still_triggers_violation(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    body = "the _legacy approach is wrong and should be removed entirely."
    artefact = ReviewArtefact(text=body, kind=ArtefactKind.CODE_DIFF)

    out = await auditor.synthesise(
        artefact,
        [_approved(AgentRole.ARCHITECT)],
        record_tenant_id=tenant_id,
        trace_id=trace_id,
    )

    assert out.verdict is ReviewVerdict.REJECTED
    assert any("version-ref" in i and "_legacy" in i for i in out.issues)
    assert len(fake.calls) == 0


@pytest.mark.asyncio
async def test_empty_artefact_text_produces_no_violation(
    fake_spec, tenant_id, trace_id
) -> None:
    fake = FakeLLM(replies=[make_reply(summary="merged", verdict="approved")])
    auditor = AuditorAgent(llm=fake, spec=fake_spec)
    artefact = ReviewArtefact(text="   \n\t  \n", kind=ArtefactKind.PLAN)

    out = await auditor.synthesise(
        artefact,
        [_approved(AgentRole.ARCHITECT)],
        record_tenant_id=tenant_id,
        trace_id=trace_id,
    )

    assert out.verdict is ReviewVerdict.APPROVED
    assert out.issues == []
    assert len(fake.calls) == 1
