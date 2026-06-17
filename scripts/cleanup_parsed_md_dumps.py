#!/usr/bin/env python3
"""Cleanup parsed-MD dump files older than the retention window.

Parsed-MD dump (`var/parsed_md/{tenant}/{doc_id}.md`) is a debug-only
convenience artefact written next to ``documents.raw_content`` so operators
can inspect parsed Markdown in a text editor. The DB row stays the source of
truth — lost file = next re-upload regenerates.

This script walks ``{PARSED_MD_DIR}/*/*.md`` and deletes files where
``Path.stat().st_mtime`` is older than ``--retention-days`` (default from
``DEFAULT_PARSED_MD_RETENTION_DAYS``). ``--dry-run`` logs without deleting.

Pattern follows ``scripts/cost_audit.py`` — stdlib-only argparse + Path.

Usage:
    python scripts/cleanup_parsed_md_dumps.py --dry-run
    python scripts/cleanup_parsed_md_dumps.py --retention-days 14
    python scripts/cleanup_parsed_md_dumps.py --root /var/parsed_md
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Local import — keep package boundary respected. Adding `src/` to sys.path so
# the script runs even when invoked from the repo root without `pip install -e .`
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ragbot.shared.constants import (  # noqa: E402  — path bootstrap above
    DEFAULT_PARSED_MD_DIR,
    DEFAULT_PARSED_MD_RETENTION_DAYS,
    DEFAULT_PARSED_MD_SUFFIX,
)


def resolve_root(explicit: str | None) -> Path | None:
    """Return the dump root directory, or ``None`` if dump is disabled.

    Resolution order:
    1. CLI ``--root`` arg (operator explicit override).
    2. Env ``RAGBOT_PARSED_MD_DIR`` (matches `parsed_md_dump.py:get_dump_root`).
    3. ``DEFAULT_PARSED_MD_DIR`` constant fallback.

    Empty env var ``RAGBOT_PARSED_MD_DIR=`` → dump disabled → returns ``None``.
    """
    if explicit is not None:
        if not explicit.strip():
            return None
        return Path(explicit).expanduser()
    raw = os.getenv("RAGBOT_PARSED_MD_DIR")
    if raw is None:
        return Path(DEFAULT_PARSED_MD_DIR)
    raw = raw.strip()
    if not raw:
        return None
    return Path(raw).expanduser()


def find_stale_files(
    root: Path,
    *,
    retention_days: int,
    now: float | None = None,
) -> list[Path]:
    """Return sorted list of dump files older than the retention window.

    Scans ``{root}/*/*<suffix>`` (two-segment tenant/doc layout). Files with
    ``st_mtime`` strictly older than ``now - retention_days*86400`` are
    flagged. Uses ``Path.stat().st_mtime`` directly — no DB lookup so the
    script is safe to run on backup servers without ragbot deps.
    """
    if retention_days < 0:
        raise ValueError(f"retention_days must be >= 0, got {retention_days}")
    cutoff = (now if now is not None else time.time()) - retention_days * 86400.0
    stale: list[Path] = []
    if not root.is_dir():
        return stale
    pattern = f"*/*{DEFAULT_PARSED_MD_SUFFIX}"
    for fp in root.glob(pattern):
        try:
            if not fp.is_file():
                continue
            if fp.stat().st_mtime < cutoff:
                stale.append(fp)
        except OSError:
            # Race: file removed between glob + stat. Skip silently.
            continue
    stale.sort()
    return stale


def cleanup(
    root: Path,
    *,
    retention_days: int,
    dry_run: bool,
    stream=sys.stdout,
) -> tuple[int, float]:
    """Run cleanup. Return (count_deleted_or_would_delete, mb_freed)."""
    files = find_stale_files(root, retention_days=retention_days)
    bytes_freed = 0
    deleted = 0
    for fp in files:
        try:
            size = fp.stat().st_size
        except OSError:
            continue
        if dry_run:
            stream.write(f"[DRY-RUN] would delete {fp} ({size} bytes)\n")
            deleted += 1
            bytes_freed += size
            continue
        try:
            fp.unlink()
            deleted += 1
            bytes_freed += size
            stream.write(f"deleted {fp} ({size} bytes)\n")
        except OSError as exc:
            stream.write(f"failed {fp}: {exc}\n")
    mb_freed = bytes_freed / (1024.0 * 1024.0)
    label = "would free" if dry_run else "freed"
    stream.write(
        f"\n{'[DRY-RUN] ' if dry_run else ''}"
        f"{deleted} file(s) {label} {mb_freed:.2f} MB "
        f"(retention={retention_days}d, root={root})\n"
    )
    return deleted, mb_freed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cleanup parsed-MD dump files older than retention window.",
    )
    p.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_PARSED_MD_RETENTION_DAYS,
        help=f"Delete files older than N days (default: {DEFAULT_PARSED_MD_RETENTION_DAYS})",
    )
    p.add_argument(
        "--root",
        type=str,
        default=None,
        help="Override dump root (default: env RAGBOT_PARSED_MD_DIR or constant)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be deleted without deleting",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_root(args.root)
    if root is None:
        sys.stderr.write(
            "parsed_md dump is disabled (RAGBOT_PARSED_MD_DIR empty) — nothing to clean\n"
        )
        return 0
    if not root.is_dir():
        sys.stderr.write(f"dump root not found: {root} — nothing to clean\n")
        return 0
    cleanup(root, retention_days=args.retention_days, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
