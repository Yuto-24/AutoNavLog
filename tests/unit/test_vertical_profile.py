from __future__ import annotations

import pytest

from autonavlog.application.vertical_profile import (
    DescentProfile,
    descent_profile_from_metadata,
)


def test_descent_profile_levels_off_before_one_minute_deceleration() -> None:
    profile = DescentProfile(
        cruise_altitude_ft_msl=5_500,
        target_altitude_ft_msl=1_500,
        descent_rate_fpm=500,
        vertical_duration_seconds=480,
        deceleration_duration_seconds=60,
    )

    assert profile.planned_duration_seconds == 540
    assert profile.altitude_at_elapsed(0) == 5_500
    assert profile.altitude_at_elapsed(240) == 3_500
    assert profile.altitude_at_elapsed(480) == 1_500
    assert profile.altitude_at_elapsed(540) == 1_500


def test_descent_profile_is_built_only_from_consistent_metadata() -> None:
    metadata = {
        "cruise_altitude_ft_msl": 5_500,
        "target_altitude_ft_msl": 1_500,
        "descent_rate_fpm": 500,
        "vertical_descent_duration_seconds": 480,
        "deceleration_duration_seconds": 60,
    }

    profile = descent_profile_from_metadata(metadata)
    assert profile is not None
    assert profile.altitude_at_elapsed(60) == pytest.approx(5_000)
    assert (
        descent_profile_from_metadata(
            metadata | {"vertical_descent_duration_seconds": 60}
        )
        is None
    )
