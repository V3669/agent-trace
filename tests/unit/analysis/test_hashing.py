import hashlib

from agenttrace.analysis.hashing import canonical_hash, sha256_hex


def test_sha256_hex_basic() -> None:
    expected = hashlib.sha256(b"hello").hexdigest()
    assert sha256_hex("hello") == expected


def test_canonical_hash_stable() -> None:
    obj = {"b": 2, "a": 1}
    h1 = canonical_hash(obj)
    h2 = canonical_hash({"a": 1, "b": 2})
    assert h1 == h2  # sort_keys ensures stable hash


def test_canonical_hash_distinct() -> None:
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_sha256_hex_empty() -> None:
    assert sha256_hex("") == hashlib.sha256(b"").hexdigest()
