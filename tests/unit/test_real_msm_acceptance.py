from __future__ import annotations

from datetime import datetime, timezone
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.msm_adapter import MsmWeatherProvider
from autonavlog.weather.real_msm_acceptance import (
    RealMsmAcceptanceConfig,
    RealMsmAcceptanceError,
    run_real_msm_acceptance,
)

VALID_TIME = datetime(2026, 7, 30, 3, tzinfo=timezone.utc)
RUN_TIME = datetime(2026, 7, 29, 18, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64
SOURCE_URLS = (
    "http://database.rish.kyoto-u.ac.jp/arch/jmadata/a.bin",
    "http://database.rish.kyoto-u.ac.jp/arch/jmadata/b.bin",
)


def _provenance(*, surface: bool = False) -> dict[str, Any]:
    trace: dict[str, Any]
    if surface:
        trace = {"temperature": [{"grid": "fixture"}]}
    else:
        trace = {"u": [{"grid": "fixture"}], "v": [{"grid": "fixture"}]}
    return {
        "initial_time_utc": RUN_TIME.isoformat(),
        "source_urls": list(SOURCE_URLS),
        "source_hashes": {SOURCE_URLS[0]: SHA_A, SOURCE_URLS[1]: SHA_B},
        "interpolation_method": (
            "bilinear,time-linear"
            if surface
            else "vertical-linear,bilinear,time-linear"
        ),
        "trace": trace,
    }


def _weather_result(request: WeatherRequest) -> WeatherResult:
    if request.kind == WeatherRequestKind.SURFACE_TEMPERATURE:
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={"temperature_k": 297.15, "temperature_c": 24.0},
            metadata={
                "source_variable": "tmp_surface",
                "provenance": _provenance(surface=True),
            },
        )
    return WeatherResult(
        request_id=request.request_id,
        availability=Availability.AVAILABLE,
        kind=request.kind,
        values={
            "u_ms": 2.0,
            "v_ms": -3.0,
            "wind_speed_kt": 7.0,
            "temperature_c": 4.0,
        },
        metadata={"provenance": _provenance()},
    )


def _real_provider_stub() -> tuple[MsmWeatherProvider, dict[str, int]]:
    provider = MsmWeatherProvider.__new__(MsmWeatherProvider)
    provider._msm = SimpleNamespace(__version__="0.2.1")
    calls = {"resolve": 0, "inspect": 0, "prepare": 0, "query": 0}

    def resolve(self: MsmWeatherProvider, requirement: Any) -> ForecastRun:
        calls["resolve"] += 1
        return ForecastRun(id="20260729180000", initial_time_utc=RUN_TIME)

    def inspect(
        self: MsmWeatherProvider,
        selected_run_id: str,
        requirement: Any,
    ) -> RunSelectionStatus:
        calls["inspect"] += 1
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=selected_run_id,
            selected_run_covers_requirement=True,
        )

    def prepare(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requirement: Any,
    ) -> PreparedForecastRun:
        calls["prepare"] += 1
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={"provider": "jma-msm-wind", "package_version": "0.2.1"},
        )

    def query(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        calls["query"] += 1
        return tuple(_weather_result(request) for request in requests)

    provider.resolve_run = MethodType(resolve, provider)
    provider.inspect_run_status = MethodType(inspect, provider)
    provider.prepare_run = MethodType(prepare, provider)
    provider.query_batch = MethodType(query, provider)
    return provider, calls


@pytest.fixture(autouse=True)
def pinned_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autonavlog.weather.real_msm_acceptance.metadata.version",
        lambda distribution: "0.2.1",
    )


def test_fake_provider_cannot_pass_real_msm_gate() -> None:
    with pytest.raises(RealMsmAcceptanceError, match="exact MsmWeatherProvider"):
        run_real_msm_acceptance(
            FakeWeatherProvider(),
            RealMsmAcceptanceConfig(),
        )


def test_offline_preflight_checks_package_without_weather_calls() -> None:
    provider, calls = _real_provider_stub()
    report = run_real_msm_acceptance(
        provider,
        RealMsmAcceptanceConfig(),
    )
    assert report["status"] == "PASS"
    assert report["mode"] == "OFFLINE_PREFLIGHT"
    assert report["live_executed"] is False
    assert calls == {"resolve": 0, "inspect": 0, "prepare": 0, "query": 0}


def test_preflight_fails_when_package_version_is_not_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autonavlog.weather.real_msm_acceptance.metadata.version",
        lambda distribution: "0.2.2",
    )
    provider, _ = _real_provider_stub()
    with pytest.raises(RealMsmAcceptanceError, match="0.2.1 exactly"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(),
        )


def test_preflight_fails_when_imported_module_version_is_not_pinned() -> None:
    provider, _ = _real_provider_stub()
    provider._msm.__version__ = "0.2.0"
    with pytest.raises(RealMsmAcceptanceError, match="0.2.1 exactly"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(),
        )


def test_live_gate_resolves_prepares_queries_and_validates_provenance() -> None:
    provider, calls = _real_provider_stub()
    report = run_real_msm_acceptance(
        provider,
        RealMsmAcceptanceConfig(
            valid_time_utc=VALID_TIME,
            live=True,
        ),
    )
    assert report["status"] == "PASS"
    assert report["mode"] == "LIVE"
    assert report["live_executed"] is True
    assert report["forecast"]["result_count"] == 4
    assert {result["kind"] for result in report["forecast"]["results"]} == {
        "ALOFT",
        "SURFACE_TEMPERATURE",
    }
    assert calls == {"resolve": 1, "inspect": 1, "prepare": 1, "query": 1}


def test_live_gate_fails_instead_of_skipping_unavailable_result() -> None:
    provider, _ = _real_provider_stub()

    def unavailable(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        request = requests[0]
        results[0] = WeatherResult(
            request_id=request.request_id,
            availability=Availability.UNAVAILABLE,
            kind=request.kind,
            reason_code="NO_DATA",
        )
        return tuple(results)

    provider.query_batch = MethodType(unavailable, provider)
    with pytest.raises(RealMsmAcceptanceError, match="not AVAILABLE"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )


def test_live_gate_fails_when_source_provenance_is_missing() -> None:
    provider, _ = _real_provider_stub()

    def missing_provenance(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        first = results[0]
        results[0] = first.model_copy(update={"metadata": {"provenance": {}}})
        return tuple(results)

    provider.query_batch = MethodType(missing_provenance, provider)
    with pytest.raises(RealMsmAcceptanceError, match="source_urls"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )


def test_live_gate_fails_when_surface_temperature_units_are_inconsistent() -> None:
    provider, _ = _real_provider_stub()

    def inconsistent_surface_temperature(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        surface_index = next(
            index
            for index, request in enumerate(requests)
            if request.kind == WeatherRequestKind.SURFACE_TEMPERATURE
        )
        surface = results[surface_index]
        results[surface_index] = surface.model_copy(
            update={"values": {"temperature_k": 297.15, "temperature_c": 20.0}}
        )
        return tuple(results)

    provider.query_batch = MethodType(inconsistent_surface_temperature, provider)
    with pytest.raises(RealMsmAcceptanceError, match="Kelvin/Celsius values are inconsistent"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )


def test_live_gate_fails_when_source_urls_and_hashes_do_not_match() -> None:
    provider, _ = _real_provider_stub()

    def mismatched_source_hashes(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        first = results[0]
        provenance = dict(first.metadata["provenance"])
        provenance["source_hashes"] = {SOURCE_URLS[0]: SHA_A}
        results[0] = first.model_copy(update={"metadata": {"provenance": provenance}})
        return tuple(results)

    provider.query_batch = MethodType(mismatched_source_hashes, provider)
    with pytest.raises(RealMsmAcceptanceError, match="source URL/hash key mismatch"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )
