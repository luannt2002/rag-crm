"""Registry-parser fast-path no longer NameErrors on document name.

A bare ``document_name`` (never defined) was referenced in the registry
routing block, so it raised NameError on every document, was swallowed by
the surrounding broad-except, and the path fell through to OCR for ALL
docs — structured parsers (Excel / CSV / DOCX) never ran. The name is now
lifted from the payload (``_doc_name``).
"""

from __future__ import annotations

import ast
import inspect

import ragbot.interfaces.workers.document_worker as worker


def test_registry_block_uses_payload_not_undefined_name() -> None:
    src = inspect.getsource(worker)
    # The fix: the doc name comes from the payload.
    assert '_doc_name = payload.get("document_name")' in src
    # No bare undefined ``document_name`` token used as a value (only
    # ``payload.get("document_name"...)`` and the separate ``doc_name``).
    assert "if document_name and" not in src, (
        "registry block must not reference the undefined `document_name`"
    )
    assert "file_name=document_name" not in src


def test_handle_inner_has_no_undefined_name_load_error() -> None:
    """AST-level: every Name read for ``document_name`` inside the worker is
    an attribute/subscript access on ``payload``, never a bare local read
    that would NameError."""
    tree = ast.parse(inspect.getsource(worker))
    # Collect bare Name nodes equal to 'document_name' used in Load context
    # that are NOT the string literal key. (The only legitimate appearances
    # are string keys "document_name" passed to payload.get.)
    bare = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id == "document_name"
        and isinstance(node.ctx, ast.Load)
    ]
    assert not bare, (
        f"{len(bare)} bare `document_name` Name load(s) remain — these "
        "NameError at runtime (use payload.get('document_name'))"
    )
