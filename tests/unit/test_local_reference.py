from __future__ import annotations

from pathlib import Path

import pytest
from scripts.local_reference import reference_state


FEED = Path(__file__).resolve().parents[1] / "fixtures/msm-portable"


@pytest.mark.parametrize(
    "options",
    [
        {"strong_wind": True, "forecast": True},
        {"strong_wind": True, "feed": str(FEED)},
    ],
)
def test_strong_wind_rejects_forecast_weather_sources(options):
    with pytest.raises(ValueError, match="strong_wind is available only for FTD weather"):
        reference_state(**options)
