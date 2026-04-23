"""Tests for RFC 8785 canonical JSON.

Test vectors verify byte-stability across Python and what TypeScript should produce.
A second test suite in cloudmorph-common-ts asserts the same vectors hash to the
same SHA-256 — proves cross-language compatibility.
"""

from __future__ import annotations

import hashlib

import pytest

from cloudmorph_common.audit.canonical_json import canonical_json, canonical_json_str


class TestCanonicalJsonBasics:
    def test_empty_object(self):
        assert canonical_json({}) == b"{}"

    def test_empty_array(self):
        assert canonical_json([]) == b"[]"

    def test_null(self):
        assert canonical_json(None) == b"null"

    def test_true_false(self):
        assert canonical_json(True) == b"true"
        assert canonical_json(False) == b"false"

    def test_string_basic(self):
        assert canonical_json("hello") == b'"hello"'

    def test_string_unicode(self):
        # ensure_ascii=False per spec; UTF-8 bytes for non-ASCII.
        assert canonical_json("café") == "café".encode("utf-8").join((b'"', b'"'))

    def test_integer(self):
        assert canonical_json(42) == b"42"
        assert canonical_json(-7) == b"-7"
        assert canonical_json(0) == b"0"

    def test_float_with_integer_value(self):
        # 5.0 must serialize as "5" per JCS §3.2.2
        assert canonical_json(5.0) == b"5"

    def test_float_real(self):
        assert canonical_json(1.5) == b"1.5"


class TestCanonicalJsonOrdering:
    def test_object_keys_sorted(self):
        out = canonical_json({"b": 2, "a": 1, "c": 3})
        assert out == b'{"a":1,"b":2,"c":3}'

    def test_nested_object_keys_sorted(self):
        out = canonical_json({"z": {"y": 1, "x": 2}, "a": 1})
        assert out == b'{"a":1,"z":{"x":2,"y":1}}'

    def test_array_order_preserved(self):
        # Arrays do NOT get reordered; JCS preserves insertion order.
        out = canonical_json([3, 1, 2])
        assert out == b"[3,1,2]"


class TestCanonicalJsonRejections:
    def test_nan_rejected(self):
        with pytest.raises(ValueError, match="NaN/Infinity"):
            canonical_json(float("nan"))

    def test_infinity_rejected(self):
        with pytest.raises(ValueError, match="NaN/Infinity"):
            canonical_json(float("inf"))

    def test_neg_infinity_rejected(self):
        with pytest.raises(ValueError, match="NaN/Infinity"):
            canonical_json(float("-inf"))

    def test_non_string_dict_key_rejected(self):
        with pytest.raises(ValueError, match="non-string key"):
            canonical_json({1: "a"})

    def test_unsupported_type(self):
        class Foo:
            pass

        with pytest.raises(TypeError, match="unsupported"):
            canonical_json(Foo())


class TestCanonicalJsonStableHashes:
    """Document the byte-output hash of standard test vectors. Cross-checked from common-ts."""

    @pytest.mark.parametrize(
        ("value", "expected_hex"),
        [
            ({}, "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"),
            ([], "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"),
            ({"a": 1, "b": 2}, "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"),
        ],
    )
    def test_sha256_stable(self, value, expected_hex):
        assert hashlib.sha256(canonical_json(value)).hexdigest() == expected_hex

    def test_str_helper_returns_str(self):
        assert canonical_json_str({"a": 1}) == '{"a":1}'
        assert isinstance(canonical_json_str({"a": 1}), str)
