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
