#!/usr/bin/env python3
"""manage_bot_vocabulary.py — bot owner CLI for ``bots.custom_vocabulary`` JSONB.

Phase C / Stream C4. Domain-neutral tool: lets a bot owner (or operator
acting on their behalf) list / get / set / remove entries inside the
``custom_vocabulary`` column on the ``bots`` table without going through
the admin HTTP API. The retrieval orchestrator already reads this column
(see ``src/ragbot/orchestration/query_graph.py`` — ``custom_vocab.get(...)``
for ``abbreviations`` / ``synonyms`` / ``diacritics``); this script only
manipulates DB state — no orchestration change.

Supported top-level categories (free-form JSONB; these are the keys the
orchestrator currently reads, but extra keys are preserved verbatim so a
bot owner can also stash ``typo_corrections`` etc.):

* ``abbreviations`` — ``{"sđt": "số điện thoại"}``
* ``diacritics`` — ``{"truong hop": "trường hợp"}``
* ``synonyms`` — ``{"sđt": ["số điện thoại", "đt"]}``
* ``typo_corrections`` — ``{"truogn": "trường"}`` (forward-compat)

Subcommands::

    list   <bot_id> --channel-type <ch>            # summarise current entries
    get    <bot_id> <category> --channel-type <ch>  # print one category as JSON
    set    <bot_id> <category> <json> --channel-type <ch>  # upsert entry(ies)
    remove <bot_id> <category> [--key <k>] --channel-type <ch>  # remove cat or single key

``--tenant-uuid`` is optional but recommended for multi-tenant deployments
(scopes the ``bots`` lookup to a specific tenant UUID — defends against
ambiguous ``(bot_id, channel_type)`` collisions across tenants). Without
it, the script requires a unique match.

Cache invalidation: after every successful mutation, the ``bots`` registry
Redis cache key ``ragbot:bot:<tenant>:<workspace>:<bot_id>:<channel>`` is
deleted so chat workers reload the updated vocabulary on the next turn.

Example::

    python scripts/manage_bot_vocabulary.py \\
        list 1774946011723 --channel-type web

    python scripts/manage_bot_vocabulary.py \\
        set 1774946011723 abbreviations '{"sđt": "số điện thoại"}' \\
        --channel-type web --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))


# Known categories — orchestrator reads these. Extra free-form keys in the
# JSONB column are preserved by the upsert / remove helpers but emit a
# warning so the owner notices typos in the top-level category name.
KNOWN_CATEGORIES: tuple[str, ...] = (
    "abbreviations",
    "diacritics",
    "synonyms",
    "typo_corrections",
)


# ---------------------------------------------------------------------------
# Pure helpers — unit-testable without DB.
# ---------------------------------------------------------------------------


def upsert_category(
    current: dict[str, Any],
    category: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Merge ``payload`` into ``current[category]`` without dropping existing keys.

    Returns a NEW dict (does not mutate ``current``). Empty ``payload`` is a
    no-op — caller decides whether that is an error. Non-string keys in the
    payload raise ``ValueError`` — JSONB stores arbitrary types but the
    orchestrator only looks up by string.
    """
    if not isinstance(current, dict):
        raise TypeError(f"current must be dict, got {type(current).__name__}")
    if not isinstance(payload, dict):
        raise TypeError(f"payload must be dict, got {type(payload).__name__}")
    for k in payload:
        if not isinstance(k, str):
            raise ValueError(f"payload keys must be str, got {type(k).__name__}: {k!r}")

    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in current.items()}
    existing = merged.get(category)
    if isinstance(existing, dict):
        existing = dict(existing)
        existing.update(payload)
        merged[category] = existing
    else:
        # Category absent OR holds a non-dict (bad data from a prior
        # malformed write). Replace with payload — owner explicitly opted
        # in by passing this category name.
        merged[category] = dict(payload)
    return merged


def remove_category(
    current: dict[str, Any],
    category: str,
    key: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Remove ``category`` (or a single ``key`` inside it).

    Returns ``(new_dict, removed_count)``. ``removed_count`` is the number
    of leaf entries actually deleted (0 if category missing / key missing).
    Never mutates ``current``.
    """
    if not isinstance(current, dict):
        raise TypeError(f"current must be dict, got {type(current).__name__}")

    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in current.items()}
    if category not in merged:
        return merged, 0

    if key is None:
        bucket = merged.pop(category)
        if isinstance(bucket, dict):
            return merged, len(bucket)
        return merged, 1  # non-dict scalar counted as one entry

    bucket = merged.get(category)
    if not isinstance(bucket, dict) or key not in bucket:
        return merged, 0
    bucket = dict(bucket)
    bucket.pop(key)
    merged[category] = bucket
    return merged, 1


def summarise(current: dict[str, Any]) -> dict[str, int]:
    """Return ``{category: entry_count}`` for the listing subcommand."""
    if not isinstance(current, dict):
        return {}
    out: dict[str, int] = {}
    for cat, bucket in current.items():
        if isinstance(bucket, dict):
            out[cat] = len(bucket)
        else:
            out[cat] = 1
    return out


def parse_json_payload(raw: str) -> dict[str, Any]:
    """Parse ``raw`` as a JSON object. Raises ``ValueError`` otherwise."""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(
            f"JSON payload must be an object, got {type(parsed).__name__}"
        )
    return parsed


# ---------------------------------------------------------------------------
# DB / Redis I/O — wrapped so the pure helpers above stay testable.
# ---------------------------------------------------------------------------


def _resolve_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL env var required")
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    return url


async def _resolve_bot_row(
    engine: Any,
    bot_id: str,
    channel_type: str,
    record_tenant_uuid: UUID | None,
) -> dict[str, Any] | None:
    """Return ``{id, record_tenant_id, workspace_id, custom_vocabulary}`` or None."""
    from sqlalchemy import text

    params: dict[str, Any] = {"b": bot_id, "c": channel_type}
    where = ["bot_id = :b", "channel_type = :c", "is_deleted = false"]
    if record_tenant_uuid is not None:
        where.append("record_tenant_id = :t")
        params["t"] = record_tenant_uuid

    sql = (
        "SELECT id, record_tenant_id, workspace_id, custom_vocabulary "
        "FROM bots WHERE " + " AND ".join(where) + " LIMIT 2"
    )
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params)
        rows = result.fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise RuntimeError(
                f"ambiguous bot lookup: {len(rows)} rows match "
                f"bot_id={bot_id!r} channel_type={channel_type!r}; "
                "pass --tenant-uuid to disambiguate"
            )
        row = rows[0]
        return {
            "id": row[0],
            "record_tenant_id": row[1],
            "workspace_id": row[2],
            "custom_vocabulary": dict(row[3] or {}),
        }


async def _write_vocab(
    engine: Any, record_bot_id: UUID, new_vocab: dict[str, Any],
) -> None:
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE bots SET custom_vocabulary = CAST(:v AS jsonb), "
                "updated_at = now() WHERE id = :b"
            ),
            {"v": json.dumps(new_vocab), "b": record_bot_id},
        )


async def _invalidate_registry_cache(
    record_tenant_id: UUID, workspace_id: str, bot_id: str, channel_type: str,
) -> int:
    """Best-effort Redis purge — non-fatal on connection error."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        from redis import asyncio as aioredis  # type: ignore
    except ImportError:
        print("[redis] redis-py missing, skipping cache invalidate", file=sys.stderr)
        return -1

    key = f"ragbot:bot:{record_tenant_id}:{workspace_id}:{bot_id}:{channel_type}"
    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        return int(await client.delete(key))
    except Exception as exc:  # noqa: BLE001 — best-effort cache flush; never block DB write
        print(f"[redis] cache invalidate failed ({exc!r})", file=sys.stderr)
        return -1
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Subcommand drivers.
# ---------------------------------------------------------------------------


async def cmd_list(args: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_resolve_db_url(), echo=False)
    try:
        record_tenant_uuid = UUID(args.tenant_uuid) if args.tenant_uuid else None
        row = await _resolve_bot_row(
            engine, args.bot_id, args.channel_type, record_tenant_uuid
        )
        if row is None:
            print(
                f"ERROR: bot not found bot_id={args.bot_id} channel_type={args.channel_type}",
                file=sys.stderr,
            )
            return 2
        counts = summarise(row["custom_vocabulary"])
        print(json.dumps({
            "record_bot_id": str(row["id"]),
            "categories": counts,
            "total_categories": len(counts),
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


async def cmd_get(args: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_resolve_db_url(), echo=False)
    try:
        record_tenant_uuid = UUID(args.tenant_uuid) if args.tenant_uuid else None
        row = await _resolve_bot_row(
            engine, args.bot_id, args.channel_type, record_tenant_uuid
        )
        if row is None:
            print(f"ERROR: bot not found", file=sys.stderr)
            return 2
        bucket = row["custom_vocabulary"].get(args.category)
        if bucket is None:
            print(f"ERROR: category {args.category!r} not present", file=sys.stderr)
            return 3
        print(json.dumps(bucket, ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


async def cmd_set(args: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    payload = parse_json_payload(args.json_payload)
    if not payload:
        print("ERROR: empty payload, nothing to upsert", file=sys.stderr)
        return 4

    if args.category not in KNOWN_CATEGORIES:
        print(
            f"[warn] category {args.category!r} is not one of {KNOWN_CATEGORIES}; "
            "orchestrator will ignore it but the data is stored verbatim",
            file=sys.stderr,
        )

    engine = create_async_engine(_resolve_db_url(), echo=False)
    try:
        record_tenant_uuid = UUID(args.tenant_uuid) if args.tenant_uuid else None
        row = await _resolve_bot_row(
            engine, args.bot_id, args.channel_type, record_tenant_uuid
        )
        if row is None:
            print(f"ERROR: bot not found", file=sys.stderr)
            return 2

        new_vocab = upsert_category(
            row["custom_vocabulary"], args.category, payload,
        )

        if not args.confirm:
            print(
                json.dumps({
                    "dry_run": True,
                    "before": row["custom_vocabulary"],
                    "after": new_vocab,
                }, ensure_ascii=False, indent=2)
            )
            print("[dry-run] add --confirm to persist.", file=sys.stderr)
            return 0

        await _write_vocab(engine, row["id"], new_vocab)
        purged = await _invalidate_registry_cache(
            row["record_tenant_id"], row["workspace_id"],
            args.bot_id, args.channel_type,
        )
        print(json.dumps({
            "ok": True,
            "category": args.category,
            "after_entry_count": len(new_vocab.get(args.category, {})),
            "redis_keys_purged": purged,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


async def cmd_remove(args: argparse.Namespace) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_resolve_db_url(), echo=False)
    try:
        record_tenant_uuid = UUID(args.tenant_uuid) if args.tenant_uuid else None
        row = await _resolve_bot_row(
            engine, args.bot_id, args.channel_type, record_tenant_uuid
        )
        if row is None:
            print(f"ERROR: bot not found", file=sys.stderr)
            return 2

        new_vocab, removed = remove_category(
            row["custom_vocabulary"], args.category, args.key,
        )
        if removed == 0:
            target = f"{args.category}.{args.key}" if args.key else args.category
            print(f"[noop] {target!r} not present, nothing removed", file=sys.stderr)
            return 5

        if not args.confirm:
            print(json.dumps({
                "dry_run": True,
                "would_remove": removed,
                "before": row["custom_vocabulary"],
                "after": new_vocab,
            }, ensure_ascii=False, indent=2))
            print("[dry-run] add --confirm to persist.", file=sys.stderr)
            return 0

        await _write_vocab(engine, row["id"], new_vocab)
        purged = await _invalidate_registry_cache(
            row["record_tenant_id"], row["workspace_id"],
            args.bot_id, args.channel_type,
        )
        print(json.dumps({
            "ok": True,
            "removed_count": removed,
            "redis_keys_purged": purged,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Arg parsing.
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--channel-type", required=True,
                        help="channel slug, e.g. 'web' / 'zalo'")
        sp.add_argument("--tenant-uuid", default=None,
                        help="record_tenant_id UUID (scopes bot lookup; "
                             "required if bot_id is ambiguous cross-tenant)")

    sp_list = sub.add_parser("list", help="show current vocabulary summary")
    sp_list.add_argument("bot_id")
    _add_common(sp_list)

    sp_get = sub.add_parser("get", help="dump one category as JSON")
    sp_get.add_argument("bot_id")
    sp_get.add_argument("category", choices=KNOWN_CATEGORIES)
    _add_common(sp_get)

    sp_set = sub.add_parser("set", help="upsert entries into a category")
    sp_set.add_argument("bot_id")
    sp_set.add_argument("category")  # not choices-bound — warn-only above
    sp_set.add_argument("json_payload",
                        help='JSON object, e.g. \'{"sđt": "số điện thoại"}\'')
    _add_common(sp_set)
    sp_set.add_argument("--confirm", action="store_true",
                        help="actually write to DB (default = dry-run)")

    sp_rm = sub.add_parser("remove", help="delete a category or a single key")
    sp_rm.add_argument("bot_id")
    sp_rm.add_argument("category")
    sp_rm.add_argument("--key", default=None,
                       help="single key inside the category; omit to drop the whole category")
    _add_common(sp_rm)
    sp_rm.add_argument("--confirm", action="store_true",
                       help="actually write to DB (default = dry-run)")

    return p


_DISPATCH = {
    "list": cmd_list,
    "get": cmd_get,
    "set": cmd_set,
    "remove": cmd_remove,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = _DISPATCH[args.cmd]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
