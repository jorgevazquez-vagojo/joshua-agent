"""Tests for HMAC signing."""
from joshua.utils.signing import sign_entry, verify_entry


def test_sign_verify_with_key():
    entry = "3|GO|0.95|2026-01-01T00:00:00"
    key = "test-secret"
    sig = sign_entry(entry, key)
    assert sig != ""
    assert verify_entry(entry, sig, key)


def test_verify_fails_wrong_key():
    entry = "3|GO|0.95|2026-01-01T00:00:00"
    sig = sign_entry(entry, "key-a")
    assert not verify_entry(entry, sig, "key-b")


def test_unsigned_always_valid():
    entry = "3|GO|0.95|2026-01-01T00:00:00"
    assert verify_entry(entry, "", "")


def test_sign_empty_key_returns_empty():
    assert sign_entry("anything", "") == ""
