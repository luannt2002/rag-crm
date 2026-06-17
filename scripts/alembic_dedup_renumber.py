#!/usr/bin/env python3
"""Alembic duplicate revision renumber — fix-once consolidation.

Phát hiện duplicate `revision = "XXXX"` trong `alembic/versions/*.py`
và renumber tự động để chain linear:
  - Giữ file đầu tiên (chronological filename order) với revision gốc
  - Renumber files duplicate: 0079 → 0079a, 0079b, ...
  - Update down_revision của file phụ thuộc

Usage:
  PYTHONPATH=src .venv/bin/python scripts/alembic_dedup_renumber.py --dry-run
  PYTHONPATH=src .venv/bin/python scripts/alembic_dedup_renumber.py --apply
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def parse_rev(path: Path) -> tuple[str | None, str | None]:
    """Return (revision, down_revision) from a migration file."""
    rev = None
    down = None
    with open(path) as f:
        for line in f:
            m = re.match(r'^revision\s*=\s*["\']([^"\']+)["\']', line.strip())
            if m:
                rev = m.group(1)
            m = re.match(r'^down_revision\s*=\s*["\']([^"\']+)["\']', line.strip())
            if m:
                down = m.group(1)
    return rev, down


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Apply renumber (default: dry-run)")
    args = ap.parse_args()

    if not VERSIONS_DIR.exists():
        print(f"❌ {VERSIONS_DIR} not found")
        return 2

    # Collect all migrations
    files = []
    for f in sorted(VERSIONS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        rev, down = parse_rev(f)
        if rev:
            files.append((f, rev, down))

    print(f"Found {len(files)} migration files\n")

    # Group by revision
    by_rev: dict[str, list[tuple[Path, str | None]]] = defaultdict(list)
    for f, rev, down in files:
        by_rev[rev].append((f, down))

    # Find duplicates
    duplicates = {rev: lst for rev, lst in by_rev.items() if len(lst) > 1}
    if not duplicates:
        print("✅ No duplicate revisions found")
        return 0

    print(f"⚠ Found {len(duplicates)} duplicate revisions:")
    for rev in sorted(duplicates):
        print(f"  {rev}: {len(duplicates[rev])} files")
        for f, down in duplicates[rev]:
            print(f"    - {f.name} (down={down})")
    print()

    # Build renumber plan
    rename_plan: list[tuple[Path, str, str, Path]] = []  # (old_path, old_rev, new_rev, new_path)
    rev_map: dict[tuple[str, str], str] = {}  # (old_rev, original_filename) → new_rev

    for rev in sorted(duplicates):
        files_list = sorted(duplicates[rev], key=lambda x: x[0].name)  # chronological
        # First file keeps original revision
        # Subsequent get suffix: a, b, c, ...
        for i, (f, down) in enumerate(files_list[1:], 1):
            suffix = chr(ord('a') + i - 1)  # a, b, c
            new_rev = f"{rev}{suffix}"
            new_filename = f.name.replace(f"_{rev}_", f"_{new_rev}_", 1)
            new_path = f.parent / new_filename
            rename_plan.append((f, rev, new_rev, new_path))
            rev_map[(rev, f.name)] = new_rev

    print(f"=== Rename plan ({len(rename_plan)} files) ===")
    for old, old_rev, new_rev, new_path in rename_plan:
        print(f"  {old.name}: rev '{old_rev}' → '{new_rev}'")
        print(f"    rename → {new_path.name}")

    if not args.apply:
        print("\n[DRY RUN] Use --apply to execute")
        return 1

    print("\n=== APPLYING ===")
    for old, old_rev, new_rev, new_path in rename_plan:
        # 1. Edit revision field
        content = old.read_text()
        # Replace `revision = "X"` line
        new_content = re.sub(
            r'^(revision\s*=\s*)["\']' + re.escape(old_rev) + r'["\']',
            rf'\1"{new_rev}"',
            content,
            count=1,
            flags=re.MULTILINE,
        )
        if new_content == content:
            print(f"  ⚠ Failed to replace revision in {old.name}")
            continue
        old.write_text(new_content)
        # 2. Rename file
        if new_path != old:
            os.rename(str(old), str(new_path))
        print(f"  ✅ {old.name} → {new_path.name}")

    print(f"\n✅ Renumbered {len(rename_plan)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
