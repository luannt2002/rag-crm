"""Demo / backend chat + admin API — package form of the former ``test_chat.py``.

Behavior-preserving split of the original 5354-line god-module into focused
route sub-modules. The importable name ``test_chat`` is preserved so
``http/router.py`` keeps mounting ``test_chat.router`` at the
``{BASE}/test`` prefix and ``test_chat.pages_router`` at root — the public URL
surface (``/api/ragbot/test/...``) is byte-identical to before the split.

Aggregation:
  * ``router``       — every API sub-module's ``APIRouter`` (chat / bot-admin /
    bot-insights / document / admin / token / monitoring).
  * ``pages_router`` — the UI HTML pages + the DEV self-token endpoint.

Re-exports: helpers, DTOs and endpoint functions that external code imports off
this module name (``chat_stream.py`` late-import; integration + unit tests).
"""

from __future__ import annotations

from fastapi import APIRouter

# --- shared helpers + pipeline-config SSoT (re-export for external importers) -
from ._shared import *  # noqa: F401,F403 — re-export _container/_sf/_build_pipeline_config/...
from ._shared import __all__ as _shared_all
from .schemas import *  # noqa: F401,F403 — re-export the 11 request DTOs
from .schemas import __all__ as _schemas_all

# --- API route sub-modules -------------------------------------------------
from . import (
    admin_routes,
    bot_admin_routes,
    bot_insights_routes,
    chat_routes,
    document_routes,
    monitoring_routes,
    token_routes,
)
from . import pages as _pages

# Endpoint functions imported by tests directly off ``test_chat.<name>``.
from .chat_routes import (  # noqa: F401
    test_chat,
    test_chat_clear,
    test_chat_history,
    test_chat_stream,
)
from .bot_admin_routes import (  # noqa: F401
    chunking_info,
    create_bot,
    delete_bot,
    get_bot_vocabulary,
    get_callback_format,
    list_bots,
    update_bot,
    update_bot_max_history,
    update_bot_vocabulary,
)
from .bot_insights_routes import (  # noqa: F401
    bot_audit_stats,
    generate_test_questions,
    quality_dashboard,
)
from .document_routes import (  # noqa: F401
    add_document,
    delete_document,
    list_documents,
    upload_document_file,
)
from .admin_routes import (  # noqa: F401
    admin_delete_api_key,
    admin_list_api_keys,
    admin_list_config,
    admin_list_models,
    admin_redis_key_detail,
    admin_redis_keys,
    admin_update_config,
    admin_upsert_api_key,
)
from .token_routes import (  # noqa: F401
    create_token,
    list_tokens,
    regenerate_token,
    revoke_token,
)
from .monitoring_routes import (  # noqa: F401
    monitoring,
    reinit_bots,
    seed_sources,
    validate_link,
)
from .pages import get_self_token  # noqa: F401


# ---------------------------------------------------------------------------
# Aggregate routers — identical route surface to the original single module.
# Order mirrors the original top-to-bottom definition order so OpenAPI output
# and any path-precedence behaviour stay unchanged.
# ---------------------------------------------------------------------------
router = APIRouter(tags=["test"])
router.include_router(bot_admin_routes.router)
router.include_router(document_routes.router)
router.include_router(monitoring_routes.router)
router.include_router(chat_routes.router)
router.include_router(bot_insights_routes.router)
router.include_router(admin_routes.router)
router.include_router(token_routes.router)

pages_router = APIRouter(tags=["pages"], include_in_schema=False)
pages_router.include_router(_pages.pages_router)


__all__ = [
    "router",
    "pages_router",
    *_shared_all,
    *_schemas_all,
    # endpoint functions imported by tests
    "test_chat",
    "test_chat_stream",
    "test_chat_history",
    "test_chat_clear",
    "list_bots",
    "create_bot",
    "update_bot",
    "get_callback_format",
    "delete_bot",
    "update_bot_max_history",
    "chunking_info",
    "get_bot_vocabulary",
    "update_bot_vocabulary",
    "bot_audit_stats",
    "generate_test_questions",
    "quality_dashboard",
    "list_documents",
    "add_document",
    "upload_document_file",
    "delete_document",
    "admin_list_config",
    "admin_update_config",
    "admin_list_api_keys",
    "admin_upsert_api_key",
    "admin_delete_api_key",
    "admin_redis_keys",
    "admin_redis_key_detail",
    "admin_list_models",
    "create_token",
    "regenerate_token",
    "revoke_token",
    "list_tokens",
    "seed_sources",
    "reinit_bots",
    "validate_link",
    "monitoring",
    "get_self_token",
]
