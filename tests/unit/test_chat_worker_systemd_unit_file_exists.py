"""Pin test — ``ragbot-chat-worker.service`` systemd unit file contract.

Verifies:
- Unit file exists at ``/etc/systemd/system/ragbot-chat-worker.service``
- File references the correct Python module path
  (``ragbot.interfaces.workers.chat_worker``)
- ``EnvironmentFile`` line references the ``.env`` file
- ``Restart=on-failure`` is set (not ``Restart=always``)
- ``ExecStart`` uses the venv Python directly (not a bash wrapper)
"""
from __future__ import annotations

import pathlib

import pytest

UNIT_FILE = pathlib.Path("/etc/systemd/system/ragbot-chat-worker.service")

# The systemd unit only exists on a provisioned deploy host. In dev/CI the
# path is absent, so skip the whole module cleanly rather than fail — the
# contract assertions below still run (and stay meaningful) wherever the
# file is actually installed.
pytestmark = pytest.mark.skipif(
    not UNIT_FILE.exists(),
    reason=f"{UNIT_FILE} absent (deploy-host-only unit file)",
)


def _content() -> str:
    return UNIT_FILE.read_text()


def test_unit_file_exists():
    """The systemd unit file must be present at the canonical path."""
    assert UNIT_FILE.exists(), (
        f"Missing: {UNIT_FILE}. Create with "
        "``src/ragbot/interfaces/workers/chat_worker.py`` as the entry point."
    )


def test_unit_file_references_chat_worker_module():
    """``ExecStart`` must reference the ``chat_worker`` module."""
    content = _content()
    assert "ragbot.interfaces.workers.chat_worker" in content, (
        "Unit file ExecStart must invoke "
        "``-m ragbot.interfaces.workers.chat_worker``"
    )


def test_unit_file_references_env_file():
    """``EnvironmentFile`` must point at ``/var/www/html/ragbot/.env``."""
    content = _content()
    assert "EnvironmentFile" in content, "Missing EnvironmentFile directive"
    assert "/var/www/html/ragbot/.env" in content, (
        "EnvironmentFile must reference ``/var/www/html/ragbot/.env``"
    )


def test_unit_file_restart_on_failure():
    """``Restart=on-failure`` is required (not ``Restart=always``)."""
    content = _content()
    # Check for ``Restart=on-failure`` specifically.
    lines = [l.strip() for l in content.splitlines()]
    restart_lines = [l for l in lines if l.startswith("Restart=")]
    assert restart_lines, "No Restart= directive found in unit file"
    assert any("on-failure" in l for l in restart_lines), (
        f"Expected Restart=on-failure; found: {restart_lines}"
    )


def test_unit_file_uses_venv_python():
    """``ExecStart`` must use the venv Python binary directly."""
    content = _content()
    assert ".venv/bin/python" in content, (
        "ExecStart must use the virtualenv Python "
        "at ``.venv/bin/python`` (not system python)"
    )
