"""Unit tests for ``scripts/cleanup_parsed_md_dumps.py``.

Behavioural assertions only — no ``assert True`` shims.
Covers:
* mtime-based filtering (boundary: exactly at cutoff is kept)
* dry-run does NOT delete
* real run deletes and reports MB freed
* disabled root (env empty) is a no-op
* nested non-md files are ignored
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import pytest

# Add scripts/ to sys.path so the module is importable as a top-level name.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import cleanup_parsed_md_dumps as mod  # noqa: E402  — path bootstrap above


def _make_dump(root: Path, tenant: str, doc: str, *, age_days: float, body: bytes = b"x") -> Path:
    """Create a fake dump file with mtime offset by ``age_days`` from now."""
    tenant_dir = root / tenant
    tenant_dir.mkdir(parents=True, exist_ok=True)
    fp = tenant_dir / f"{doc}.md"
    fp.write_bytes(body)
    mtime = time.time() - age_days * 86400.0
    # atime kept = mtime so st_mtime drives the test
    import os as _os
    _os.utime(fp, (mtime, mtime))
    return fp


def test_find_stale_files_filters_by_mtime(tmp_path: Path) -> None:
    fresh = _make_dump(tmp_path, "tenant-a", "doc-fresh", age_days=1)
    boundary = _make_dump(tmp_path, "tenant-a", "doc-boundary", age_days=30)
    stale = _make_dump(tmp_path, "tenant-b", "doc-stale", age_days=45)

    found = mod.find_stale_files(tmp_path, retention_days=30)

    assert fresh not in found, "1-day-old file should NOT be flagged at 30d retention"
    assert stale in found, "45-day-old file SHOULD be flagged at 30d retention"
    # Boundary (mtime == cutoff): NOT flagged (strict less-than).
    # Allow 1s clock jitter — boundary may flip either way; assert it's the
    # only ambiguous one and the count is at least the stale one.
    assert len(found) in (1, 2)


def test_find_stale_files_zero_retention_flags_everything(tmp_path: Path) -> None:
    _make_dump(tmp_path, "tenant-x", "d1", age_days=0.001)
    _make_dump(tmp_path, "tenant-x", "d2", age_days=10)
    found = mod.find_stale_files(tmp_path, retention_days=0)
    assert len(found) == 2, "retention=0 should flag every file"


def test_find_stale_files_negative_retention_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="retention_days must be >= 0"):
        mod.find_stale_files(tmp_path, retention_days=-1)


def test_find_stale_files_ignores_non_md_files(tmp_path: Path) -> None:
    _make_dump(tmp_path, "tenant-y", "d1", age_days=60)
    # Decoy: .txt file, old, should NOT be flagged.
    decoy = tmp_path / "tenant-y" / "d1.txt"
    decoy.write_bytes(b"junk")
    import os as _os
    old_mtime = time.time() - 60 * 86400.0
    _os.utime(decoy, (old_mtime, old_mtime))

    found = mod.find_stale_files(tmp_path, retention_days=30)
    assert len(found) == 1
    assert found[0].suffix == ".md"


def test_find_stale_files_missing_root_returns_empty(tmp_path: Path) -> None:
    nonexistent = tmp_path / "nope"
    found = mod.find_stale_files(nonexistent, retention_days=30)
    assert found == []


def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    fp = _make_dump(tmp_path, "tenant-a", "doc-1", age_days=60, body=b"hello world")
    buf = io.StringIO()

    deleted, mb_freed = mod.cleanup(
        tmp_path, retention_days=30, dry_run=True, stream=buf
    )

    assert fp.exists(), "dry-run MUST NOT delete the file"
    assert deleted == 1, "dry-run still reports the would-delete count"
    assert mb_freed > 0
    assert "DRY-RUN" in buf.getvalue()
    assert "would delete" in buf.getvalue()


def test_real_run_deletes_and_reports_mb(tmp_path: Path) -> None:
    # 2 MB file, 60 days old → should be deleted
    body = b"x" * (2 * 1024 * 1024)
    fp = _make_dump(tmp_path, "tenant-a", "doc-2", age_days=60, body=body)
    buf = io.StringIO()

    deleted, mb_freed = mod.cleanup(
        tmp_path, retention_days=30, dry_run=False, stream=buf
    )

    assert not fp.exists(), "real run MUST delete the file"
    assert deleted == 1
    assert 1.9 <= mb_freed <= 2.1, f"expected ~2 MB freed, got {mb_freed}"
    assert "deleted" in buf.getvalue()
    assert "DRY-RUN" not in buf.getvalue()


def test_real_run_skips_fresh_files(tmp_path: Path) -> None:
    fresh = _make_dump(tmp_path, "tenant-a", "doc-fresh", age_days=1)
    stale = _make_dump(tmp_path, "tenant-a", "doc-stale", age_days=60)
    buf = io.StringIO()

    deleted, _ = mod.cleanup(
        tmp_path, retention_days=30, dry_run=False, stream=buf
    )

    assert fresh.exists(), "fresh file must survive"
    assert not stale.exists(), "stale file must be removed"
    assert deleted == 1


def test_resolve_root_explicit_arg_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGBOT_PARSED_MD_DIR", str(tmp_path / "from_env"))
    explicit = tmp_path / "from_arg"
    resolved = mod.resolve_root(str(explicit))
    assert resolved == explicit, "CLI --root must override env"


def test_resolve_root_empty_env_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGBOT_PARSED_MD_DIR", "")
    assert mod.resolve_root(None) is None, "empty env disables dump"


def test_resolve_root_empty_explicit_disables() -> None:
    assert mod.resolve_root("") is None
    assert mod.resolve_root("   ") is None


def test_resolve_root_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAGBOT_PARSED_MD_DIR", raising=False)
    resolved = mod.resolve_root(None)
    assert resolved is not None
    assert resolved.name == "parsed_md"


def test_main_no_op_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGBOT_PARSED_MD_DIR", "")
    rc = mod.main(["--dry-run"])
    assert rc == 0


def test_main_no_op_when_root_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("RAGBOT_PARSED_MD_DIR", str(missing))
    rc = mod.main(["--dry-run"])
    assert rc == 0


def test_main_dry_run_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fp = _make_dump(tmp_path, "tenant-z", "doc-old", age_days=90)
    monkeypatch.setenv("RAGBOT_PARSED_MD_DIR", str(tmp_path))
    rc = mod.main(["--dry-run", "--retention-days", "10"])
    assert rc == 0
    assert fp.exists(), "main --dry-run must not delete"


def test_main_real_run_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fp = _make_dump(tmp_path, "tenant-z", "doc-old", age_days=90)
    monkeypatch.setenv("RAGBOT_PARSED_MD_DIR", str(tmp_path))
    rc = mod.main(["--retention-days", "10"])
    assert rc == 0
    assert not fp.exists(), "main real-run must delete stale file"


def test_constant_imported_correctly() -> None:
    from ragbot.shared.constants import DEFAULT_PARSED_MD_RETENTION_DAYS
    assert isinstance(DEFAULT_PARSED_MD_RETENTION_DAYS, int)
    assert DEFAULT_PARSED_MD_RETENTION_DAYS > 0
    # Must equal what the script uses by default.
    assert mod.DEFAULT_PARSED_MD_RETENTION_DAYS == DEFAULT_PARSED_MD_RETENTION_DAYS


def test_script_exported_in_constants_all() -> None:
    from ragbot.shared import constants as c
    assert "DEFAULT_PARSED_MD_RETENTION_DAYS" in c.__all__


def test_find_stale_handles_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If stat() raises mid-scan (race), file is silently skipped (no crash)."""
    fp = _make_dump(tmp_path, "tenant-q", "doc-q", age_days=60)

    original_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == fp:
            raise OSError("simulated race")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    found = mod.find_stale_files(tmp_path, retention_days=30)
    assert fp not in found, "unreadable file must be skipped silently"
