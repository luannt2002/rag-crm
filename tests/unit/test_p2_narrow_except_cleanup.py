"""P2 polish guard — broad-except cleanup over the input-data / chunking surface.

P2 scope: every broad-catch in the ingest / chunking code path must either be
narrowed to specific library exception types or carry an explicit
``# noqa: BLE001 — <reason>`` justification. These tests pin that invariant so a
future edit cannot reintroduce an un-justified blind catch in the input-data
flow.

All assertions are behavior-neutral (they inspect source text / run the linter);
they do not exercise runtime ingest behavior, which is covered elsewhere.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent.parent / "src" / "ragbot"

# The input-data / chunking surface this P2 sweep is responsible for. Each path
# is a directory or single module on the canonical ingest → chunk → quality flow.
_INPUT_DATA_PATHS = (
    SRC_ROOT / "shared" / "chunking",
    SRC_ROOT / "shared" / "chunk_quality.py",
    SRC_ROOT / "infrastructure" / "parser",
    SRC_ROOT / "infrastructure" / "chunking_strategy",
    SRC_ROOT / "infrastructure" / "chunk_quality",
    SRC_ROOT / "application" / "services" / "document_service",
)

_INGEST_PHASES = (
    SRC_ROOT / "application" / "services" / "document_service" / "ingest_phases.py"
)

# Matches either ``except Exception`` or ``except BaseException`` (optionally
# with an ``as <name>`` binding), so the BaseException form ruff flags is caught
# too — the project's other guard only matches the ``except Exception`` spelling.
_BROAD_EXCEPT_RE = re.compile(r"except\s+(Base)?Exception(\s*:|\s+as\s+\w+\s*:)")
_NOQA_RE = re.compile(r"#\s*noqa(:|\b)")


def _iter_py(paths) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(
                f for f in p.rglob("*.py") if "__pycache__" not in f.parts
            )
        elif p.is_file():
            files.append(p)
    return files


def _ruff_available() -> bool:
    return shutil.which("ruff") is not None


def test_targeted_basexcept_in_ingest_phases_is_noqa_annotated() -> None:
    """The async-CM body-arm BaseException catch carries a justified noqa.

    This is the single genuinely-required broad-catch on the ingest surface:
    it must route base-class body exceptions through ``cm.__aexit__`` and then
    re-raise via the ``_body_propagate`` sentinel. Narrowing would break that
    contract, so the P2 fix is a ``# noqa: BLE001`` with a reason — assert it
    is present and reasoned (em-dash separates the rule code from the why).
    """
    assert _INGEST_PHASES.is_file(), f"missing source file: {_INGEST_PHASES}"
    text = _INGEST_PHASES.read_text(encoding="utf-8")

    body_arm = [
        line
        for line in text.splitlines()
        if "except BaseException as _body_exc:" in line
    ]
    assert len(body_arm) == 1, (
        "expected exactly one ``except BaseException as _body_exc:`` body-arm "
        f"in ingest_phases.py, found {len(body_arm)}"
    )
    line = body_arm[0]
    assert _NOQA_RE.search(line), (
        "the async-CM body-arm BaseException catch must carry a "
        "``# noqa: BLE001`` annotation (P2 sweep); narrowing it would drop "
        f"BaseException propagation. Line: {line!r}"
    )
    assert "BLE001" in line, f"noqa must name BLE001. Line: {line!r}"
    # A reason must follow the rule code (em-dash or hyphen separator).
    assert re.search(r"BLE001\s*[—-]\s*\S", line), (
        f"noqa must include a reason after BLE001. Line: {line!r}"
    )


def test_named_chunking_sites_broad_except_are_justified() -> None:
    """chunk_quality.py + chunking/strategies.py broad-catches stay noqa-justified.

    These are the prompt-named sites. Each broad ``except`` on the chunking
    happy-path must be narrowed (no broad form) OR carry a ``# noqa: BLE001``.
    Behaviour-identical guard — inspects source text only.
    """
    targets = (
        SRC_ROOT / "shared" / "chunk_quality.py",
        SRC_ROOT / "shared" / "chunking" / "strategies.py",
    )
    offenders: list[str] = []
    for f in targets:
        assert f.is_file(), f"missing source file: {f}"
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _BROAD_EXCEPT_RE.search(line) and not _NOQA_RE.search(line):
                offenders.append(f"{f.name}:{n}: {line.strip()}")
    assert not offenders, (
        "un-justified broad-except on the named chunking sites — narrow the "
        "type or add ``# noqa: BLE001 — <reason>``:\n" + "\n".join(offenders)
    )


def test_no_unjustified_broad_except_on_input_data_surface() -> None:
    """Regression guard: zero un-noqa broad/Base-except across the ingest surface.

    Scans the whole input-data / chunking file set with a regex that matches
    BOTH ``except Exception`` and ``except BaseException`` spellings. Any new
    blind catch added without a ``# noqa`` justification fails here.
    """
    offenders: list[str] = []
    for f in _iter_py(_INPUT_DATA_PATHS):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _BROAD_EXCEPT_RE.search(line) and not _NOQA_RE.search(line):
                rel = f.relative_to(SRC_ROOT)
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, (
        "un-justified broad-except on the input-data surface — narrow the type "
        "or add ``# noqa: BLE001 — <reason>``:\n" + "\n".join(offenders)
    )


@pytest.mark.skipif(not _ruff_available(), reason="ruff not installed")
def test_ruff_ble001_clean_on_input_data_surface() -> None:
    """Authoritative lint check: ruff reports zero BLE001 on the ingest surface.

    Robust to line-number drift — delegates to the linter the project already
    uses for the broad-except rule. Expects ``All checks passed!``.
    """
    args = ["ruff", "check", "--select", "BLE001"] + [
        str(p) for p in _INPUT_DATA_PATHS if p.exists()
    ]
    proc = subprocess.run(args, capture_output=True, text=True)
    assert proc.returncode == 0, (
        "ruff BLE001 found un-justified broad-except on the input-data "
        f"surface:\n{proc.stdout}\n{proc.stderr}"
    )
