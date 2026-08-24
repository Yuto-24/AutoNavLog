from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from decimal import Decimal
from uuid import UUID

import pytest

from autonavlog.application.fingerprints import canonical_json, make_fingerprint


def _vector(rounded_float: float) -> dict[str, object]:
    return {
        "int_value": 42,
        "float_value": 1.0,
        "rounded_float": rounded_float,
        "negative_zero": -0.0,
        "bool_value": True,
        "none_value": None,
        "text": "宮崎",
        "uuid": UUID("0f4f2a34-2f0e-4a3c-8b2a-1d9f6e5c4b3a"),
        "when": datetime(2026, 8, 2, 0, 0, tzinfo=UTC),
        "on": date(2026, 8, 2),
        "nested": {"b": [1, 2.5], "a": {"deep": "x"}},
        "as_set": {"b", "a", "c"},
    }


def test_fixed_fingerprint_vectors() -> None:
    expected_payload = (
        '{"fields":{"as_set":["a","b","c"],"bool_value":true,'
        '"float_value":"1.000000","int_value":42,'
        '"negative_zero":"0.000000","nested":{"a":{"deep":"x"},'
        '"b":[1,"2.500000"]},"none_value":null,"on":"2026-08-02",'
        '"rounded_float":"0.123456","text":"宮崎",'
        '"uuid":"0f4f2a34-2f0e-4a3c-8b2a-1d9f6e5c4b3a",'
        '"when":"2026-08-02T00:00:00.000000Z"},'
        '"fingerprint_version":1,"kind":"test_vector"}'
    )
    assert canonical_json(kind="test_vector", fields=_vector(0.12345649)) == (expected_payload)
    assert (
        make_fingerprint(
            kind="test_vector",
            fields=_vector(0.12345649),
        )
        == "cbf72b4daefa6a82e1e68be85b92b7783a52a0573a3f42b9362f8790fef291bd"
    )
    assert (
        make_fingerprint(
            kind="test_vector",
            fields=_vector(0.12345651),
        )
        == "dcc631c0d34cc59a19f412895b9ed1d13f07b22a8d19c38bd5237a6a8fd3a5ff"
    )
    assert (
        make_fingerprint(
            kind="test_vector",
            fields=_vector(0.1234565),
        )
        == "cbf72b4daefa6a82e1e68be85b92b7783a52a0573a3f42b9362f8790fef291bd"
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_floats_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        make_fingerprint(kind="invalid", fields={"value": value})


def test_unsupported_or_ambiguous_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_fingerprint(
            kind="invalid",
            fields={"value": datetime(2026, 8, 2)},
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        make_fingerprint(
            kind="invalid",
            fields={
                "value": datetime(2026, 8, 2).replace(tzinfo=tzinfo()),
            },
        )
    with pytest.raises(TypeError):
        make_fingerprint(kind="invalid", fields={"value": Decimal("1.0")})
    with pytest.raises(TypeError):
        make_fingerprint(kind="invalid", fields={"value": object()})
    with pytest.raises(ValueError, match="safe range"):
        make_fingerprint(kind="invalid", fields={"value": 2**53})


def test_normalized_key_collisions_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate key"):
        make_fingerprint(kind="invalid", fields={1: "int", "1": "str"})
