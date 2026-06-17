#!/usr/bin/env python3
"""Linearize alembic chain — force single-head by wiring orphans sequential.

After alembic_dedup_renumber.py creates suffixed revisions (0078a, 0079a, ...),
those orphan branches still have ``down_revision = "0077"`` which causes
multi-head. This script wires them sequentially:

  0077 → 0078 → 0078a → 0078b → 0079 → 0079a → 0080 → 0080a → ... → final

Usage:
  PYTHONPATH=src .venv/bin/python scripts/alembic_linearize_chain.py --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def rev_sort_key(rev: str) -> tuple:
    """Sort key: '0078' < '0078a' < '0078b' < '0079'."""
    m = re.match(r'^(\d+)([a-z]?)', rev)
    if m:
        num, suffix = m.groups()
        return (int(num), suffix or "")
    return (999999, rev)


def parse_rev(path: Path) -> tuple[str | None, str | None]:
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


def set_down_revision(path: Path, new_down: str) -> bool:
    """Update down_revision in-place."""
    content = path.read_text()
    new_content = re.sub(
        r'^(down_revision\s*=\s*)["\'][^"\']+["\']',
        rf'\1"{new_down}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    if new_content == content:
        return False
    path.write_text(new_content)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    # Collect all migrations
    revs: list[tuple[Path, str, str | None]] = []
    for f in sorted(VERSIONS_DIR.glob("*.py")):
        if f.name == "__init__.py":
            continue
        rev, down = parse_rev(f)
        if rev:
            revs.append((f, rev, down))

    # Sort by revision (with suffix)
    revs.sort(key=lambda x: rev_sort_key(x[1]))

    print(f"Found {len(revs)} migration files. Linearizing chain...\n")

    # Build new chain: each revision's down = previous revision
    plan: list[tuple[Path, str, str | None, str]] = []  # (path, rev, old_down, new_down)
    prev_rev = None
    for f, rev, down in revs:
        # Don't touch the very first revision (0001)
        if prev_rev is None:
            print(f"  {rev}: base (down={down}, unchanged)")
            prev_rev = rev
            continue
        new_down = prev_rev
        if down != new_down:
            plan.append((f, rev, down, new_down))
            print(f"  {rev}: down='{down}' → '{new_down}'  [{f.name}]")
        else:
            print(f"  {rev}: down='{down}' (unchanged)")
        prev_rev = rev

    if not plan:
        print("\n✅ Chain already linear")
        return 0

    print(f"\n{len(plan)} changes needed.")
    if not args.apply:
        print("[DRY RUN] Use --apply to execute")
        return 1

    print("\n=== APPLYING ===")
    for f, rev, old_down, new_down in plan:
        if set_down_revision(f, new_down):
            print(f"  ✅ {rev}: down='{old_down}' → '{new_down}'")
        else:
            print(f"  ⚠ Failed to update {f.name}")
    print(f"\n✅ Applied {len(plan)} changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
