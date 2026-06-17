"""Tests for retry_with_backoff and CircuitBreaker."""

import pytest

from ragbot.application.services.retry_policy import (
    CBState,
    CircuitBreaker,
    CircuitBreakerPolicy,
    RetryPolicy,
    retry_with_backoff,
)
from ragbot.shared.errors import CircuitBreakerOpen


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_retries_on_failure_then_succeeds(self):
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("temporary")
            return "ok"

        result = await retry_with_backoff(
            flaky, policy=RetryPolicy(max_attempts=3, initial_backoff_ms=10)
        )
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        async def always_fail():
            raise ConnectionError("permanent")

        with pytest.raises(ConnectionError):
            await retry_with_backoff(
                always_fail, policy=RetryPolicy(max_attempts=2, initial_backoff_ms=10)
            )


class TestCircuitBreaker:
    def test_opens_after_fail_max(self):
        cb = CircuitBreaker(name="test", policy=CircuitBreakerPolicy(fail_max=3))
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CBState.OPEN

    def test_closed_allows_execution(self):
        cb = CircuitBreaker(name="test")
        assert cb.can_execute() is True

    def test_open_blocks_execution(self):
        cb = CircuitBreaker(name="test", policy=CircuitBreakerPolicy(fail_max=1))
        cb.record_failure()
        assert cb.state == CBState.OPEN
        assert cb.can_execute() is False

    def test_context_manager_raises_when_open(self):
        cb = CircuitBreaker(name="test", policy=CircuitBreakerPolicy(fail_max=1))
        cb.record_failure()
        with pytest.raises(CircuitBreakerOpen):
            with cb:
                pass

    def test_success_resets_circuit(self):
        cb = CircuitBreaker(name="test", policy=CircuitBreakerPolicy(fail_max=2))
        cb.record_failure()
        cb.record_success()
        assert cb.state == CBState.CLOSED
        assert cb._state.fail_count == 0


class TestCircuitBreakerHalfOpen:
    def test_transitions_to_half_open_after_timeout(self):
        import datetime
        from unittest.mock import patch

        cb = CircuitBreaker(
            name="test",
            policy=CircuitBreakerPolicy(fail_max=1, reset_timeout_s=1),
        )
        cb.record_failure()
        assert cb.state == CBState.OPEN
        assert cb.can_execute() is False

        # After reset_timeout_s, should transition to HALF_OPEN
        future = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(seconds=2)
        with patch.object(cb, '_now', return_value=future):
            assert cb.can_execute() is True
            assert cb.state == CBState.HALF_OPEN

    def test_half_open_success_closes_circuit(self):
        cb = CircuitBreaker(
            name="test",
            policy=CircuitBreakerPolicy(fail_max=1, reset_timeout_s=0),
        )
        cb.record_failure()
        # Force half_open
        cb._state.state = CBState.HALF_OPEN
        cb.record_success()
        assert cb.state == CBState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(
            name="test",
            policy=CircuitBreakerPolicy(fail_max=1, reset_timeout_s=0),
        )
        cb._state.state = CBState.HALF_OPEN
        cb.record_failure()
        assert cb.state == CBState.OPEN
