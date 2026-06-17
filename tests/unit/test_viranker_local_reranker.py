"""Unit tests for ``infrastructure.reranker.viranker_local_reranker``.

ViRanker is registered as an opt-in STUB: visible in ``list_providers()`` so
operators can see the option, but constructing it raises until the heavy
cross-encoder dep is installed. Tests pin:

- Stub raises NotImplementedError with an actionable hint on construct.
- Provider name + mode tag are stable for audit logs.
- Registry advertises ``"viranker_local"`` after the opt-in flip.
- Fail-soft: registry catches the NotImplementedError and falls back to
  ``NullReranker`` so a misconfig in ``system_config`` cannot crash boot.

The heavy model is **NEVER loaded** in these tests — they only touch the
stub class metadata and registry wiring.
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.reranker import (
    NullReranker,
    build_reranker,
    list_providers,
)
from ragbot.infrastructure.reranker.viranker_local_reranker import ViRankerLocalReranker


def test_stub_constructor_raises_with_install_hint() -> None:
    with pytest.raises(NotImplementedError) as exc:
        ViRankerLocalReranker()

    msg = str(exc.value).lower()
    # Hint must point operators toward the actual fix path.
    assert "sentence-transformers" in msg
    assert "viranker" in msg


def test_get_provider_name_is_stable() -> None:
    # Class-level metadata for audit log tagging — must work without
    # constructing the (heavy) instance.
    assert ViRankerLocalReranker.get_provider_name() == "viranker_local"


def test_registry_advertises_viranker_local() -> None:
    # Confirms the opt-in registry uncomment landed.
    providers = list_providers()
    assert "viranker_local" in providers
    # And the canonical baseline strategies remain.
    for required in ("null", "litellm", "jina"):
        assert required in providers


def test_registry_falls_back_to_null_when_stub_init_fails() -> None:
    # Operator typos ``reranker_provider="viranker_local"`` without installing
    # the heavy dep -> registry catches NotImplementedError and returns
    # NullReranker so the box still boots and the misconfig is observable
    # via the warning log.
    rr = build_reranker("viranker_local")
    assert isinstance(rr, NullReranker)


def test_registry_filter_does_not_pass_extra_kwargs() -> None:
    # The registry kwargs filter must not pass globally-applied kwargs
    # (e.g. ``api_key=``) into the ViRanker stub ctor — its only accepted
    # kwarg is ``model``. We cannot construct directly (raises), but we
    # CAN call build_reranker with extra kwargs and confirm fall-back.
    rr = build_reranker("viranker_local", api_key="should-be-filtered-out")
    assert isinstance(rr, NullReranker)
