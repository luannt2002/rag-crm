"""Wave M3.3-F — audit ``system_config`` for duplicate-concept key drift.

Run periodically (CI cron or manual ops sweep) to detect rows whose
key names hint at duplicate concepts (e.g. ``top_k_retrieve`` vs
``rag_top_k``). Drift surfaces silently — production reads ONE key,
Pareto sweep / analytics reads the OTHER, and verdicts diverge.

Usage::

    set -a && source .env && set +a
    python scripts/audit_config_key_drift.py

Exit codes:
    0 — no drift detected
    1 — drift found, report printed to stdout

Domain-neutral: no bot literals in patterns; only naming-convention
hints. Add a new pair to ``_SUSPECT_PAIRS`` when the team notices a
rename pattern that needs CI guarding.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

# pylint: disable=wrong-import-position
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src")

from sqlalchemy import create_engine, text


# Pairs of (legacy_key, canonical_key) the project has explicitly renamed.
# Both rows existing simultaneously means analytics / production read
# different values for the same concept. Extend as new renames land.
_SUSPECT_PAIRS: tuple[tuple[str, str], ...] = (
    # mega-sprint-G21 rename — production swapped to ``rag_*`` prefix
    # but Pareto sweep kept legacy keys until Wave M3.3-D fix.
    ("top_k_retrieve", "rag_top_k"),
    ("top_k_rerank", "rag_rerank_top_n"),
)


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL_APP") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "DATABASE_URL_APP or DATABASE_URL required (run `set -a; source .env; set +a`)."
        )
    return dsn.replace("postgresql+asyncpg", "postgresql+psycopg2")


def _scan(pairs: Iterable[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    engine = create_engine(_dsn())
    with engine.connect() as conn:
        for legacy, canonical in pairs:
            rows = conn.execute(
                text("SELECT key, value FROM system_config WHERE key = ANY(:k)"),
                {"k": [legacy, canonical]},
            ).fetchall()
            row_map = {r[0]: r[1] for r in rows}
            if legacy in row_map and canonical in row_map:
                findings.append(
                    {
                        "type": "duplicate_pair",
                        "legacy_key": legacy,
                        "legacy_value": row_map[legacy],
                        "canonical_key": canonical,
                        "canonical_value": row_map[canonical],
                        "advice": (
                            "DELETE the legacy row via a migration; "
                            "production code reads the canonical key only."
                        ),
                    }
                )
            elif legacy in row_map:
                findings.append(
                    {
                        "type": "legacy_only",
                        "legacy_key": legacy,
                        "value": row_map[legacy],
                        "advice": (
                            f"Rename to canonical key '{canonical}' via migration; "
                            "production code does NOT read this key."
                        ),
                    }
                )
    return findings


def main() -> int:
    findings = _scan(_SUSPECT_PAIRS)
    if not findings:
        print("OK — no system_config key drift detected.")
        return 0
    print("DRIFT DETECTED in system_config:")
    print()
    for f in findings:
        if f["type"] == "duplicate_pair":
            print(
                f"  ⚠️  {f['legacy_key']}={f['legacy_value']!r}  AND  "
                f"{f['canonical_key']}={f['canonical_value']!r}"
            )
        else:
            print(f"  ⚠️  legacy-only: {f['legacy_key']}={f['value']!r}")
        print(f"      advice: {f['advice']}")
        print()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
