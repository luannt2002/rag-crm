"""_check_workers — embedded-worker liveness signal for /health.

A supervised embedded worker exits ONLY on crash (embedded_workers._supervise
logs + returns, never auto-restarts), so a completed task = a dead consumer.
These pins guard the four states: disabled (dep omitted), all-alive, one-dead,
and enabled-but-none-spawned (misconfig).
"""

from types import SimpleNamespace

from ragbot.interfaces.http.routes.health import _check_workers


class _Task:
    def __init__(self, name: str, done: bool) -> None:
        self._name = name
        self._done = done

    def done(self) -> bool:
        return self._done

    def get_name(self) -> str:
        return self._name


def _req(tasks: list) -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(embedded_worker_tasks=tasks))
    )


def _settings(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(embed_workers_enabled=enabled))


def test_disabled_returns_none_so_dep_is_omitted() -> None:
    # API-only node: workers run as separate processes → never flag degraded.
    assert _check_workers(_req([]), _settings(False)) is None


def test_all_alive_is_ok() -> None:
    tasks = [_Task("consumer", False), _Task("outbox", False)]
    assert _check_workers(_req(tasks), _settings(True)) == "ok"


def test_one_dead_worker_is_down() -> None:
    tasks = [_Task("consumer", False), _Task("cache_purge", True)]
    assert _check_workers(_req(tasks), _settings(True)) == "down"


def test_enabled_but_none_spawned_is_down() -> None:
    # Misconfiguration: embed enabled but the lifespan spawned nothing.
    assert _check_workers(_req([]), _settings(True)) == "down"


def test_missing_state_attr_is_down_when_enabled() -> None:
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    assert _check_workers(req, _settings(True)) == "down"
