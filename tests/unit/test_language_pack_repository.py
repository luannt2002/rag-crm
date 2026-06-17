"""Tests for ``LanguagePackRepository``.

The repository is a thin wrapper around two SELECT statements; the
critical contract is that ``get_pack`` returns ``None`` (not raise,
not empty string) when the row is missing, and ``list_pack`` returns
an empty dict (not ``None``) on the same condition. We verify both via
in-memory fakes that mimic the SQLAlchemy session protocol so the test
suite stays infra-free.
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.infrastructure.repositories.language_pack_repository import (
    LanguagePackRepository,
)


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]] | None) -> None:
        self._rows = rows

    def fetchone(self) -> tuple[Any, ...] | None:
        if not self._rows:
            return None
        return self._rows[0]

    def fetchall(self) -> list[tuple[Any, ...]]:
        return list(self._rows or [])


class _FakeSession:
    def __init__(self, table: dict[tuple[str, str], str]) -> None:
        self._table = table

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: dict[str, Any]) -> _FakeResult:
        sql = str(stmt).lower()
        if "where code = :c and prompt_key = :k" in sql:
            row = self._table.get((params["c"], params["k"]))
            return _FakeResult([(row,)] if row is not None else None)
        if "where code = :c" in sql:
            rows = [
                (k, v) for (c, k), v in self._table.items() if c == params["c"]
            ]
            return _FakeResult(rows)
        raise AssertionError(f"unexpected sql: {sql}")


class _FakeSessionFactory:
    def __init__(self, table: dict[tuple[str, str], str]) -> None:
        self._table = table

    def __call__(self) -> _FakeSession:
        return _FakeSession(self._table)


@pytest.mark.asyncio
async def test_get_pack_returns_content_for_known_row() -> None:
    table = {
        ("vi", "generator"): "vi-gen-text",
        ("vi", "grader"): "vi-grader-text",
    }
    repo = LanguagePackRepository(_FakeSessionFactory(table))
    assert await repo.get_pack("vi", "generator") == "vi-gen-text"


@pytest.mark.asyncio
async def test_get_pack_returns_none_when_missing() -> None:
    repo = LanguagePackRepository(_FakeSessionFactory({}))
    assert await repo.get_pack("vi", "generator") is None


@pytest.mark.asyncio
async def test_list_pack_returns_dict_of_all_keys_for_language() -> None:
    table = {
        ("vi", "generator"): "vi-gen",
        ("vi", "grader"): "vi-gra",
        ("en", "generator"): "en-gen",
    }
    repo = LanguagePackRepository(_FakeSessionFactory(table))
    pack = await repo.list_pack("vi")
    assert pack == {"generator": "vi-gen", "grader": "vi-gra"}


@pytest.mark.asyncio
async def test_list_pack_returns_empty_dict_when_language_unseeded() -> None:
    repo = LanguagePackRepository(_FakeSessionFactory({}))
    assert await repo.list_pack("es") == {}
