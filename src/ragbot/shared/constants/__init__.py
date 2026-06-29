"""Shared constants — SSoT for runtime defaults (split into domain modules).

Per-bot overrides flow through DB (system_config / bots.plan_limits).
Re-exports every constant so `from ragbot.shared.constants import X` is unchanged.
"""
from __future__ import annotations

from ._00_app_env_taxonomy import *  # noqa: F401,F403
from ._01_http_db_client_construction_ import *  # noqa: F401,F403
from ._02_per_intent_rerank_skip_gate_ import *  # noqa: F401,F403
from ._03_language_packs_db_driven_pro import *  # noqa: F401,F403
from ._04_jwt_auth import *  # noqa: F401,F403
from ._05_embedding_circuitbreaker import *  # noqa: F401,F403
from ._06_llm_defaults import *  # noqa: F401,F403
from ._07_llm_sampling_defaults import *  # noqa: F401,F403
from ._08_sentry_otel import *  # noqa: F401,F403
from ._09_message_feedback_thumbs_verd import *  # noqa: F401,F403
from ._10_rbac import *  # noqa: F401,F403
from ._11_table_csv_chunking_strategy import *  # noqa: F401,F403
from ._12_multi_stage_retrieval_fallba import *  # noqa: F401,F403
from ._13_adapchunk_layer_1_ocr_parser import *  # noqa: F401,F403
from ._14_anti_abuse_ip_rate_limit_hon import *  # noqa: F401,F403
from ._15_m2_neighbor_window_expansion import *  # noqa: F401,F403
from ._16_prompt_token_squeeze_phase_b import *  # noqa: F401,F403
from ._17_260509_a1_pipeline_audit_6_c import *  # noqa: F401,F403
from ._18_admin_all_tenants_analytics_ import *  # noqa: F401,F403
from ._19_sprint3_ekimetrics_selector_ import *  # noqa: F401,F403
from ._20_cag_mode_cache_augmented_gen import *  # noqa: F401,F403
from ._21_streaming_upload_wb_2_p1_5 import *  # noqa: F401,F403
from ._22_conversation_state_memory import *  # noqa: F401,F403
from ._23_crm_analytics_readlayer_ import *  # noqa: F401,F403
from ._24_structural_markers_by_lang import *  # noqa: F401,F403
from ._25_locale_structure_packs import *  # noqa: F401,F403
from ._26_narrate_prompt_locale_pack import *  # noqa: F401,F403

# Underscore-prefixed constants (import * skips these) — re-export explicitly
from ._04_jwt_auth import _REFUSE_ANSWER_TYPES  # noqa: F401

# Rebuilt __all__ — complete public export list (was partial in the monolith).
# Excludes the re-exported typing.Final symbol; includes the one underscore const.
__all__ = sorted(
    n for n in list(globals()) if not n.startswith("_") and n != "Final"
) + ["_REFUSE_ANSWER_TYPES"]
