from __future__ import annotations

import math

import pytest

from autonavlog.nav.variation import (
    VARIATION_RULE_VERSION,
    variation_for_departure_latitude,
)


@pytest.mark.parametrize(
    ("latitude_deg", "expected_variation", "expected_band"),
    [
        (31.999999, 7.0, "SOUTH"),
        (32.0, 8.0, "NORTH"),
        (32.000001, 8.0, "NORTH"),
    ],
)
def test_variation_uses_north_inclusive_32n_boundary(
    latitude_deg: float,
    expected_variation: float,
    expected_band: str,
) -> None:
    decision = variation_for_departure_latitude(latitude_deg)

    assert decision.degrees_east == expected_variation
    assert decision.metadata == {
        "rule_version": VARIATION_RULE_VERSION,
        "method": "LEG_DEPARTURE_LATITUDE_BAND",
        "departure_latitude_deg": latitude_deg,
        "threshold_latitude_deg": 32.0,
        "threshold_inclusive_side": "NORTH",
        "selected_band": expected_band,
        "degrees_east": expected_variation,
    }


@pytest.mark.parametrize("latitude_deg", [math.nan, math.inf, -math.inf, -90.1, 90.1])
def test_variation_rejects_invalid_latitude_without_fallback(latitude_deg: float) -> None:
    with pytest.raises(
        ValueError,
        match=r"departure latitude must be finite and within \[-90, 90\]",
    ):
        variation_for_departure_latitude(latitude_deg)
