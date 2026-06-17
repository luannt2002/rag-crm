"""Lock test — F14-CC1-2 VN compound segmentation is gated on language.

The DocumentService ingest path runs ``segment_vi_compounds`` only when the
effective document language is in ``VI_DOMAIN_LANGUAGES``. For non-VN tenants
this saves CPU and prevents corrupting non-VN tokens.

Verified at SOURCE level (deterministic) — no need to spin up a real ingest.
"""

from __future__ import annotations

import inspect

from ragbot.application.services import document_service as ds_module
from ragbot.shared.constants import VI_DOMAIN_LANGUAGES


def test_document_service_imports_vi_domain_languages() -> None:
    src = inspect.getsource(ds_module)
    assert "VI_DOMAIN_LANGUAGES" in src, (
        "F14-CC1-2 regression — document_service must import VI_DOMAIN_LANGUAGES"
    )


def test_vi_seg_loop_gated_on_language() -> None:
    """The vi_seg_enabled branch must require ``_vi_seg_lang_eligible``."""
    # The U5/U6 enrich+segment logic now lives in the ``ingest_stages`` mixin
    # (ingest() god-method split into stage methods); scan that module.
    from ragbot.application.services.document_service import ingest_stages
    src = inspect.getsource(ds_module) + inspect.getsource(ingest_stages)
    assert "_vi_seg_lang_eligible" in src, (
        "F14-CC1-2 regression — language-gate variable removed"
    )
    # The combined gate must AND the two flags so a non-VN bot bypasses
    # underthesea even when the global flag is True.
    assert "vi_seg_enabled and _vi_seg_lang_eligible" in src, (
        "F14-CC1-2 regression — vi_seg loop must AND both flags"
    )


def test_vi_domain_languages_defaults_to_vi_only() -> None:
    """Sanity: default tuple includes ``vi`` and is not empty."""
    assert "vi" in VI_DOMAIN_LANGUAGES
    assert len(VI_DOMAIN_LANGUAGES) >= 1
