from __future__ import annotations

import pytest
from pydantic import ValidationError

from autonavlog.application.navlog_display import _bearing
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.project import ManualWind
from autonavlog.web.models import SectionUpdate


@pytest.mark.parametrize("value", [1, 359])
def test_manual_wind_accepts_integer_compass_directions(value: int) -> None:
    assert ManualWind(direction_deg_from=value, speed_kt=10).direction_deg_from == value


def test_manual_wind_accepts_360_and_normalizes_north_to_zero() -> None:
    assert ManualWind(direction_deg_from=360, speed_kt=10).direction_deg_from == 0


@pytest.mark.parametrize("value", [0, 1.5])
def test_manual_wind_rejects_zero_and_fractional_directions(value: float) -> None:
    with pytest.raises(ValidationError):
        ManualWind(direction_deg_from=value, speed_kt=10)


def test_section_update_uses_same_direction_contract() -> None:
    update = SectionUpdate(
        section_id="00000000-0000-0000-0000-000000000001",
        planned_altitude_ft_msl=5000,
        phase=FlightPhase.CRUISE,
        manual_wind_direction_deg=360,
        manual_wind_speed_kt=10,
    )
    assert update.manual_wind_direction_deg == 0
    with pytest.raises(ValidationError):
        SectionUpdate(
            section_id="00000000-0000-0000-0000-000000000001",
            planned_altitude_ft_msl=5000,
            phase=FlightPhase.CRUISE,
            manual_wind_direction_deg=0,
            manual_wind_speed_kt=10,
        )


@pytest.mark.parametrize(
    ("value", "text"),
    [(0.0, "360"), (360.0, "360"), (1.0, "001"), (359.0, "359")],
)
def test_bearing_display_uses_001_through_360(value: float, text: str) -> None:
    assert _bearing(value) == text
