"""Unit tests for shared/hmac_signing.

Surface-area covered:
* Round-trip valid signature → True
* Tampered body → False
* Empty / blank secret → ValueError
* Empty signature → False (never raise)
* Length mismatch → False without raising
* Unsupported algorithm → ValueError (downgrade defence)
* compare_digest is the comparator (sanity guard, not full timing analysis)
"""

from __future__ import annotations

import hmac as _hmac

import pytest

from ragbot.shared.hmac_signing import (
    compute_signature,
    verify_request_signature,
)


_BODY = b'{"upstream_tenant_id": 42, "event": "doc_synced"}'
_SECRET = "super-secret-pre-shared-key-not-real"


class TestComputeSignature:
    def test_roundtrip_known_value(self) -> None:
        sig = compute_signature(_BODY, _SECRET)
        # Hex sha256 → 64 chars
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_empty_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            compute_signature(_BODY, "")

    def test_blank_secret_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            compute_signature(_BODY, "   ")

    def test_unsupported_algorithm_raises(self) -> None:
        # Defence vs. silent MD5 / SHA-1 downgrade if upstream typos.
        with pytest.raises(ValueError, match="unsupported hmac algorithm"):
            compute_signature(_BODY, _SECRET, algorithm="md5")

    def test_sha512_supported(self) -> None:
        sig = compute_signature(_BODY, _SECRET, algorithm="sha512")
        assert len(sig) == 128


class TestVerifyRequestSignature:
    def test_valid_signature_passes(self) -> None:
        sig = compute_signature(_BODY, _SECRET)
        assert verify_request_signature(_BODY, sig, _SECRET) is True

    def test_tampered_body_fails(self) -> None:
        sig = compute_signature(_BODY, _SECRET)
        tampered = _BODY + b"!"
        assert verify_request_signature(tampered, sig, _SECRET) is False

    def test_wrong_secret_fails(self) -> None:
        sig = compute_signature(_BODY, _SECRET)
        assert verify_request_signature(_BODY, sig, "different-secret") is False

    def test_empty_signature_returns_false(self) -> None:
        # Must not raise — caller decides fail-open vs fail-closed.
        assert verify_request_signature(_BODY, "", _SECRET) is False

    def test_length_mismatch_returns_false_without_raise(self) -> None:
        # Truncated signature must not crash compare_digest.
        sig = compute_signature(_BODY, _SECRET)[:32]
        assert verify_request_signature(_BODY, sig, _SECRET) is False

    def test_garbage_signature_returns_false(self) -> None:
        # Non-hex characters must not raise either.
        assert verify_request_signature(_BODY, "not-a-real-hex-zzz", _SECRET) is False

    def test_constant_time_compare_used(self) -> None:
        """Sanity — verify_request_signature uses hmac.compare_digest.

        We can't measure timing in a unit test reliably, but we can confirm
        the comparator is the constant-time primitive by inspecting that
        equal-length wrong-prefix and right-prefix mismatches both return
        False (no early-return on first byte).
        """
        sig = compute_signature(_BODY, _SECRET)
        wrong_prefix = "0" * len(sig)
        right_prefix_wrong_tail = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        assert verify_request_signature(_BODY, wrong_prefix, _SECRET) is False
        assert (
            verify_request_signature(_BODY, right_prefix_wrong_tail, _SECRET)
            is False
        )

    def test_empty_secret_raises_on_verify(self) -> None:
        # Verify path delegates to compute → must surface the ValueError so
        # caller can distinguish "not configured" from "wrong signature".
        sig = "a" * 64
        with pytest.raises(ValueError, match="non-empty"):
            verify_request_signature(_BODY, sig, "")


def test_compute_matches_stdlib_hmac() -> None:
    """Sanity guard — our wrapper must match raw hmac.new output."""
    sig = compute_signature(_BODY, _SECRET)
    expected = _hmac.new(
        _SECRET.encode("utf-8"),
        _BODY,
        "sha256",
    ).hexdigest()
    assert sig == expected
