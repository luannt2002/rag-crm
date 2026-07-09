"""Config-completeness gate — every ``system_config`` key the backend
batch-loads MUST have a value in the seed, else it silently falls back to
the code ``DEFAULT_*`` constant (drift: prod runs on a value nobody chose
in the DB, unreproducible on a fresh clone).

This is the init-test the deploy pipeline runs AFTER ``alembic upgrade head``
on a freshly-seeded database and BEFORE building the image (see
``README_DEVOPS.md`` §1). A red gate is a seed gap the DATABASE team fixes by
adding the value (``README_DATABASE.md``) — never a backend inline default
(``README_DEV.md``).

The contract surface = ``_PIPELINE_CFG_KEYS`` in
``interfaces/http/routes/test_chat/_pipeline_config.py`` — the tuple that gates
which ``system_config`` rows ``cfg_svc.get_many()`` batch-loads. If a key is in
that tuple but not seeded, ``get_many()`` returns nothing for it and every
``_pcfg(state, "<key>", DEFAULT)`` call site falls through to ``DEFAULT`` forever.

Baseline discipline (decreasing-only, mirrors
``tests/unit/test_narrow_exception_hierarchy.py``): keys currently resolving
from the constant are recorded in ``config_constant_fallback_baseline.txt``.
The gate passes if no NEW contract key is unseeded; it fails the moment someone
adds a contract key without seeding it. Shrink the baseline as the DATABASE team
seeds each key (or reclassifies it as pure-technical and removes it from the
contract tuple).

Usage::

    set -a && source .env && set +a
    python scripts/check_config_completeness.py            # gate (baseline-aware)
    python scripts/check_config_completeness.py --strict   # every contract key must be seeded
    python scripts/check_config_completeness.py --write-baseline   # regenerate baseline from current DB

Exit codes:
    0 — no NEW unseeded contract key (gate green)
    1 — NEW unseeded contract key(s), or --strict and any unseeded

Domain-neutral: no bot/brand literal; operates purely on the contract tuple +
seeded key set.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PIPELINE_CFG = (
    _REPO_ROOT
    / "src" / "ragbot" / "interfaces" / "http" / "routes" / "test_chat"
    / "_pipeline_config.py"
)
_BASELINE = Path(__file__).resolve().parent / "config_constant_fallback_baseline.txt"


def _contract_keys() -> set[str]:
    """Keys in ``_PIPELINE_CFG_KEYS`` — the system_config batch-load contract."""
    text_src = _PIPELINE_CFG.read_text(encoding="utf-8")
    open_marker = re.search(
        r"^_PIPELINE_CFG_KEYS\s*:\s*tuple\b.*?=\s*\(\s*$", text_src, re.MULTILINE
    )
    if not open_marker:
        raise SystemExit(
            f"could not locate _PIPELINE_CFG_KEYS tuple in {_PIPELINE_CFG}"
        )
    rest = text_src[open_marker.end():]
    close = re.search(r"^\)\s*$", rest, re.MULTILINE)
    if not close:
        raise SystemExit("could not find close of _PIPELINE_CFG_KEYS tuple")
    body = rest[: close.start()]
    # Strip comments so commented-out (disabled) keys are NOT counted as contract
    # — a `# "foo",` line is not loaded at runtime, so the gate must ignore it too.
    body = "\n".join(line.split("#", 1)[0] for line in body.splitlines())
    return set(re.findall(r'["\']([a-zA-Z_][\w.]*)["\']', body))


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL_APP") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "DATABASE_URL_APP or DATABASE_URL required "
            "(run `set -a; source .env; set +a`)."
        )
    return dsn.replace("postgresql+asyncpg", "postgresql+psycopg2")


def _seeded_keys() -> set[str]:
    engine = create_engine(_dsn())
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT key FROM system_config")).fetchall()
    return {r[0] for r in rows}


def _load_baseline() -> set[str]:
    if not _BASELINE.exists():
        return set()
    return {
        line.strip()
        for line in _BASELINE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _write_baseline(missing: set[str]) -> None:
    header = (
        "# Config keys the backend batch-loads from system_config but that are\n"
        "# NOT seeded — each currently resolves from its code DEFAULT_* constant.\n"
        "# This is the drift backlog: the DATABASE team should seed each value\n"
        "# (README_DATABASE.md), or the key should be reclassified pure-technical\n"
        "# and removed from _PIPELINE_CFG_KEYS. DECREASING-ONLY: never add a key\n"
        "# here to silence the gate — seed it instead. Regenerate only when the\n"
        "# set genuinely shrinks: python scripts/check_config_completeness.py --write-baseline\n"
    )
    _BASELINE.write_text(header + "\n".join(sorted(missing)) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true",
                    help="every contract key must be seeded (ignore baseline)")
    ap.add_argument("--write-baseline", action="store_true",
                    help="regenerate the baseline file from the current DB")
    args = ap.parse_args()

    contract = _contract_keys()
    seeded = _seeded_keys()
    missing = contract - seeded

    if args.write_baseline:
        _write_baseline(missing)
        print(f"wrote baseline: {len(missing)} unseeded contract keys → {_BASELINE.name}")
        return 0

    baseline = set() if args.strict else _load_baseline()
    new_missing = missing - baseline
    stale_baseline = baseline - contract  # baseline entries no longer in contract

    print(f"contract keys (_PIPELINE_CFG_KEYS): {len(contract)}")
    print(f"seeded in system_config:            {len(seeded)}")
    print(f"unseeded (fall back to constant):   {len(missing)}")
    if not args.strict:
        print(f"  of which known-baseline backlog:  {len(missing & baseline)}")
        print(f"  of which NEW (gate-blocking):     {len(new_missing)}")

    if stale_baseline:
        print("\nBaseline entries no longer in the contract (seed them or drop from baseline):")
        for k in sorted(stale_baseline):
            print(f"  STALE  {k}")

    if new_missing:
        label = "UNSEEDED" if args.strict else "NEW UNSEEDED"
        print(f"\n{label} contract key(s) — seed a value (README_DATABASE.md) "
              f"or remove from _PIPELINE_CFG_KEYS:")
        for k in sorted(new_missing):
            print(f"  ✗ {k}")
        return 1

    print("\nOK — no new unseeded contract key. Gate green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
