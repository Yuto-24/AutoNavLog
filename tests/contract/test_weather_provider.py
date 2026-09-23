from __future__ import annotations

import sys
from datetime import UTC, datetime
from fractions import Fraction
from types import ModuleType, SimpleNamespace

import pytest

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.msm_adapter import MsmWeatherProvider


def test_fake_provider_fixes_selected_run_and_preserves_request_ids() -> None:
    provider = FakeWeatherProvider(("20260728120000", "20260728090000"))
    requirement = ForecastRequirement(valid_times_utc=(datetime(2026, 7, 29, tzinfo=UTC),))
    status = provider.inspect_run_status("20260728090000", requirement)
    assert status.selected_run_id == "20260728090000"
    assert status.update_available
    provider.prepare_run(status.selected_run_id, requirement)
    request = WeatherRequest(
        request_id="q1",
        kind=WeatherRequestKind.ALOFT,
        latitude_deg=31,
        longitude_deg=131,
        valid_time_utc=datetime(2026, 7, 29, tzinfo=UTC),
        altitude_ft_msl=5000,
    )
    assert provider.query_batch(status.selected_run_id, [request])[0].request_id == "q1"


def _stub_msm_module() -> ModuleType:
    module = ModuleType("jma_gpv_weather")
    module.__version__ = "0.5.0"
    module.WeatherVariable = SimpleNamespace(
        ALOFT_WIND="wind",
        ALOFT_TEMPERATURE="temperature",
        SURFACE_TEMPERATURE="surface_temperature",
    )
    module.Availability = SimpleNamespace(AVAILABLE="available")

    class ForecastRequirements:
        def __init__(self, valid_times, variables):
            self.valid_times = valid_times
            self.variables = variables

    class RunId:
        def __init__(self, initial_time_utc):
            self.initial_time_utc = initial_time_utc

        def __str__(self):
            return self.initial_time_utc.strftime("%Y%m%d%H%M%S")

    class AloftQuery:
        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    class EstimatedQnhQuery(AloftQuery):
        pass

    class Terrain:
        @classmethod
        def load(cls, path):
            return cls()

    module.ForecastRequirements = ForecastRequirements
    module.RunId = RunId
    module.AloftQuery = AloftQuery
    module.SurfaceTemperatureQuery = EstimatedQnhQuery
    module.GridTerrainProvider = Terrain
    return module


def test_msm_adapter_allows_aloft_only_without_terrain(
    monkeypatch,
    tmp_path,
) -> None:
    module = _stub_msm_module()
    monkeypatch.setitem(sys.modules, "jma_gpv_weather", module)
    captured_terrain = object()

    class Client:
        def prepare_run(self, run_id, requirement, terrain_provider=None):
            nonlocal captured_terrain
            captured_terrain = terrain_provider
            return SimpleNamespace(query_many=lambda queries: [])

    provider = MsmWeatherProvider(tmp_path, client=Client())
    requirement = ForecastRequirement(
        valid_times_utc=(datetime(2026, 7, 29, tzinfo=UTC),),
    )

    provider.prepare_run("20260728120000", requirement)

    assert captured_terrain is None


def test_msm_adapter_samples_lsurf_temperature_with_provenance(
    monkeypatch,
    tmp_path,
) -> None:
    module = _stub_msm_module()
    monkeypatch.setitem(sys.modules, "jma_gpv_weather", module)
    captured_variables: frozenset[str] = frozenset()

    class Prepared:
        def query_many(self, queries):
            assert len(queries) == 1
            latitude, longitude, valid_time = queries[0].args
            assert (latitude, longitude) == (31.877, 131.448)
            return (
                SimpleNamespace(
                    availability="available",
                    reason_code=None,
                    warnings=("NOT_FOR_OPERATIONAL_USE",),
                    values={"temperature_k": 298.15, "temperature_c": 25.0},
                    provenance={
                        "interpolation_method": "bilinear,time-linear",
                        "trace": {
                            "temperature": [
                                {"valid_time": valid_time.isoformat(), "latitude": Fraction(255, 8)}
                            ],
                        },
                    },
                ),
            )

    class Client:
        def prepare_run(self, run_id, requirement, terrain_provider=None):
            nonlocal captured_variables
            captured_variables = requirement.variables
            assert terrain_provider is None
            return Prepared()

    provider = MsmWeatherProvider(tmp_path, client=Client())
    requirement = ForecastRequirement(
        valid_times_utc=(datetime(2026, 7, 29, tzinfo=UTC),),
        require_surface_temperature=True,
    )
    prepared = provider.prepare_run("20260728120000", requirement)
    request = WeatherRequest(
        request_id="departure:surface",
        kind=WeatherRequestKind.SURFACE_TEMPERATURE,
        latitude_deg=31.877,
        longitude_deg=131.448,
        valid_time_utc=datetime(2026, 7, 29, tzinfo=UTC),
        elevation_ft_msl=20,
    )

    (result,) = provider.query_batch("20260728120000", (request,))

    assert "surface_temperature" in captured_variables
    assert prepared.metadata["surface_temperature_required"] is True
    assert result.availability == Availability.AVAILABLE
    assert result.kind == WeatherRequestKind.SURFACE_TEMPERATURE
    assert result.values["temperature_k"] == 298.15
    assert result.values["temperature_c"] == pytest.approx(25.0)
    assert result.metadata["source_variable"] == "tmp_surface"
    assert result.metadata["requested_elevation_ft_msl"] == 20
    assert result.metadata["requested_valid_time_utc"] == ("2026-07-29T00:00:00+00:00")
    assert result.metadata["provenance"]["interpolation_method"] == ("bilinear,time-linear")
    assert result.metadata["provenance"]["trace"]["temperature"][0]["latitude"] == 31.875
    result.model_dump_json()

    unavailable = provider._from_result(
        request,
        SimpleNamespace(
            availability="unavailable",
            values={},
            warnings=(),
            provenance={},
            reason_code="MISSING_SOURCE_VALUE",
        ),
    )
    assert unavailable.availability == Availability.UNAVAILABLE
    assert unavailable.values == {"temperature_c": None}
    assert unavailable.reason_code == "SURFACE_TEMPERATURE_UNAVAILABLE"

    wrong_unit = provider._from_result(
        request,
        SimpleNamespace(
            availability="available",
            values={"temperature_k": 25.0},
            warnings=(),
            provenance={},
            reason_code=None,
        ),
    )
    assert wrong_unit.availability == Availability.UNAVAILABLE
    assert wrong_unit.reason_code == "SURFACE_TEMPERATURE_INVALID"


@pytest.mark.parametrize(
    'speed_ms,speed_kt,direction,warning,available,normalized',
    [
        (0.0, 0.0, None, True, True, True),
        (0.07906136508871421, 0.15368299909253264, None, True, True, True),
        (0.099999, 0.19438, None, True, True, True),
        (0.1, 0.194384, None, True, True, False),
        (2.0, 3.88768, None, False, True, False),
        (0.079, 0.154, None, False, True, False),
        (0.079, 0.154, 282.0, True, True, False),
        (0.079, 0.154, None, True, False, False),
        (-0.01, -0.02, None, True, True, False),
        (float('nan'), 0.154, None, True, True, False),
        (0.079, float('inf'), None, True, True, False),
        (0.079, 2.0, None, True, True, False),
        (None, 0.154, None, True, True, False),
        (False, 0.154, None, True, True, False),
    ],
)
def test_msm_calm_normalization_preserves_source_and_failure(
    monkeypatch, tmp_path, speed_ms, speed_kt, direction, warning, available, normalized,
) -> None:
    monkeypatch.setitem(sys.modules, 'jma_gpv_weather', _stub_msm_module())
    provider = MsmWeatherProvider(tmp_path, client=object())
    request = WeatherRequest(
        request_id='calm', kind=WeatherRequestKind.ALOFT,
        latitude_deg=32.07245440868941, longitude_deg=131.45257761837706,
        valid_time_utc=datetime(2026, 9, 24, 4, 7, tzinfo=UTC), altitude_ft_msl=2759.5,
    )
    raw = SimpleNamespace(
        availability='available' if available else 'unavailable',
        reason_code=None if available else 'MISSING_SOURCE_VALUE',
        warnings=('CALM_WIND_DIRECTION_UNDEFINED',) if warning else (),
        values={'wind_speed_ms': speed_ms, 'wind_speed_kt': speed_kt,
                'wind_direction_deg_from': direction, 'temperature_c': 19.877212780635944,
                'u_ms': -0.07367847895593366, 'v_ms': -0.02867370203568071},
        provenance={'run': '20260923030000'},
    )
    result = provider._from_result(request, raw)
    expected = Availability.AVAILABLE if available else Availability.UNAVAILABLE
    assert result.availability == expected
    assert result.reason_code == raw.reason_code
    assert result.warnings == raw.warnings
    assert result.metadata['provenance'] == raw.provenance
    assert ('calm_normalization' in result.metadata) is normalized
    if normalized:
        assert result.values['wind_speed_kt'] == 0.0
        assert result.metadata['calm_normalization']['original_values'] == raw.values
        assert raw.values['wind_speed_kt'] == speed_kt
    else:
        assert result.values == raw.values
