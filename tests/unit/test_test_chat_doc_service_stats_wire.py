"""Pin test: HTTP /documents/upload route wires stats_index_repo into DocumentService.

Was a known gap after F17 stats-vs-vector race shipped — only sync.py + worker
wired the repo; the test_chat HTTP upload route went through `_doc_service`
which constructed DocumentService without it, so `document_service_index`
stayed empty for any doc uploaded via the demo UI.
"""

from __future__ import annotations

import inspect

from ragbot.interfaces.http.routes import test_chat as _route


def test_doc_service_factory_passes_stats_index_repo() -> None:
    """`_doc_service(request)` MUST pass stats_index_repo kwarg to DocumentService.

    Source-level pin so a future refactor cannot silently drop the wire.
    """
    src = inspect.getsource(_route._doc_service)
    assert "stats_index_repo=" in src, (
        "_doc_service must forward stats_index_repo to DocumentService — "
        "otherwise HTTP /documents/upload writes 0 rows to document_service_index."
    )
    assert 'hasattr(c, "stats_index_repo")' in src, (
        "_doc_service must guard the DI hook with hasattr so older test "
        "containers without the provider keep working."
    )
