from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.msm_adapter import MsmWeatherProvider


def test_fake_provider_fixes_selected_run_and_preserves_request_ids() -> None:
    provider = FakeWeatherProvider(("20260728120000", "20260728090000"))
    requirement = ForecastRequirement(
        valid_times_utc=(datetime(2026, 7, 29, tzinfo=timezone.utc),)
    )
    status = provider.inspect_run_status("20260728090000", requirement)
    assert status.selected_run_id == "20260728090000"
    assert status.update_available
    provider.prepare_run(status.selected_run_id, requirement)
    request = WeatherRequest(
        request_id="q1",
        kind=WeatherRequestKind.ALOFT,
        latitude_deg=31,
        longitude_deg=131,
        valid_time_utc=datetime(2026, 7, 29, tzinfo=timezone.utc),
        altitude_ft_msl=5000,
    )
    assert provider.query_batch(status.selected_run_id, [request])[0].request_id == "q1"


def _stub_msm_module() -> ModuleType:
    module = ModuleType("msm_wind")
    module.__version__ = "0.2.1"
    module.WeatherVariable = SimpleNamespace(
        ALOFT_WIND="wind",
        ALOFT_TEMPERATURE="temperature",
        ESTIMATED_QNH="qnh",
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
    module.EstimatedQnhQuery = EstimatedQnhQuery
    module.GridTerrainProvider = Terrain
    return module


def test_msm_adapter_maps_batch_and_qnh_label(monkeypatch, tmp_path) -> None:
    module = _stub_msm_module()
    monkeypatch.setitem(sys.modules, "msm_wind", module)
    run = module.RunId(datetime(2026, 7, 28, tzinfo=timezone.utc))

    class Prepared:
        def query_many(self, queries):
            return [
                SimpleNamespace(
                    availability="available",
                    values={"label": "MSM-derived estimated QNH", "qnh_hpa": 1008.2},
                    reason_code=None,
                    warnings=("ESTIMATED_QNH_NOT_OFFICIAL",),
                    provenance=SimpleNamespace(trace={"grid": "fixture"}),
                )
            ]

    class Client:
        def resolve_run(self, requirement, selected_run=None):
            return SimpleNamespace(
                selected_run=selected_run or run,
                latest_compatible_run=run,
                selected_run_covers_request=True,
                update_available=False,
                warnings=(),
            )

        def prepare_run(self, run_id, requirement, terrain_provider=None):
            return Prepared()

    provider = MsmWeatherProvider(tmp_path, client=Client())
    requirement = ForecastRequirement(
        valid_times_utc=(datetime(2026, 7, 29, tzinfo=timezone.utc),)
    )
    resolved = provider.resolve_run(requirement)
    provider.prepare_run(resolved.id, requirement)
    request = WeatherRequest(
        request_id="qnh",
        kind=WeatherRequestKind.ESTIMATED_QNH,
        latitude_deg=31,
        longitude_deg=131,
        valid_time_utc=datetime(2026, 7, 29, tzinfo=timezone.utc),
        elevation_ft_msl=20,
    )
    result = provider.query_batch(resolved.id, [request])[0]
    assert result.request_id == "qnh"
    assert result.availability == Availability.AVAILABLE
    assert result.values["label"] == "MSM推定QNH"
    assert result.values["provider_label"] == "MSM-derived estimated QNH"
