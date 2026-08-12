from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)
from autonavlog.weather.msm_metar_provider import MsmMetarWeatherProvider
from autonavlog.weather.msm_metar_trend_provider import MsmMetarTrendQnhProvider

OBSERVED = datetime(2026, 8, 12, 18, tzinfo=timezone.utc)
TARGET = OBSERVED + timedelta(hours=24)
RUN_ID = "20260812150000"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Transport:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
        self.calls.append(url)
        return self.payload


class MslpDelegate:
    package_version = "test"

    def __init__(self, values: Mapping[datetime, float | None]) -> None:
        self.values = values
        self.prepared: list[ForecastRequirement] = []

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        self.resolved = requirement
        return ForecastRun(id=RUN_ID, initial_time_utc=OBSERVED - timedelta(hours=3))

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=selected_run_id,
            selected_run_covers_requirement=True,
        )

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        self.prepared.append(requirement)
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={"provider": "fixture-mslp"},
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        results = []
        for request in requests:
            value = self.values.get(request.valid_time_utc)
            results.append(
                WeatherResult(
                    request_id=request.request_id,
                    availability=(
                        Availability.AVAILABLE if value is not None else Availability.UNAVAILABLE
                    ),
                    kind=request.kind,
                    values=(
                        {"mslp_hpa": value, "qnh_hpa": value}
                        if value is not None
                        else {"qnh_hpa": None}
                    ),
                    reason_code=None if value is not None else "MSM_MSLP_UNAVAILABLE",
                    metadata={"valid_time": request.valid_time_utc.isoformat()},
                )
            )
        return tuple(results)


def payload(*, raw_qnh: int = 1008, altim: float = 1008.0) -> bytes:
    return json.dumps(
        [
            {
                "icaoId": "RJFM",
                "obsTime": OBSERVED.timestamp(),
                "reportTime": OBSERVED.isoformat(),
                "altim": altim,
                "rawOb": f"METAR RJFM 121800Z AUTO 28011KT 9999 Q{raw_qnh:04d}",
            },
            {
                "icaoId": "RJFO",
                "obsTime": OBSERVED.timestamp(),
                "reportTime": OBSERVED.isoformat(),
                "altim": 1007,
                "rawOb": "METAR RJFO 121800Z AUTO 27008KT 9999 Q1007",
            },
        ]
    ).encode()


def request() -> WeatherRequest:
    return WeatherRequest(
        request_id="project:qnh",
        kind=WeatherRequestKind.ESTIMATED_QNH,
        latitude_deg=31.877,
        longitude_deg=131.448,
        valid_time_utc=TARGET,
        elevation_ft_msl=20,
        metadata={"station_icao": "RJFM"},
    )


def provider(
    delegate: MslpDelegate,
    transport: Transport,
    clock: Clock,
) -> MsmMetarTrendQnhProvider:
    metar = MsmMetarWeatherProvider(
        delegate,
        transport=transport,
        clock=clock,
        cache_ttl_seconds=300,
    )
    return MsmMetarTrendQnhProvider(delegate, metar_provider=metar)


def requirement() -> ForecastRequirement:
    return ForecastRequirement(valid_times_utc=(TARGET,))


def test_metar_corrected_msm_formula_and_run_covers_both_times() -> None:
    delegate = MslpDelegate({OBSERVED: 1005.0, TARGET: 1008.5})
    transport = Transport(payload())
    weather = provider(delegate, transport, Clock(OBSERVED + timedelta(hours=1)))

    run = weather.resolve_run(requirement())
    prepared = weather.prepare_run(run.id, requirement())
    result = weather.query_batch(run.id, (request(),))[0]

    assert OBSERVED in delegate.resolved.valid_times_utc
    assert OBSERVED in delegate.prepared[0].valid_times_utc
    assert result.values["qnh_hpa"] == 1011.5
    assert result.values["qnh_method"] == "METAR_TREND_CORRECTED"
    assert result.values["msm_tendency_hpa"] == 3.5
    assert result.values["metar_observation_time_utc"] == OBSERVED.isoformat()
    assert result.values["forecast_time_utc"] == TARGET.isoformat()
    assert result.warnings == (
        "ESTIMATED_QNH_NOT_OFFICIAL",
        "VERIFY_WITH_OFFICIAL_AERODROME_QNH",
    )
    assert prepared.metadata["terrain_required"] is False
    assert len(transport.calls) == 1
    assert "ids=RJFM,RJFO" in transport.calls[0]


def test_stale_metar_falls_back_to_msm_mslp_only() -> None:
    delegate = MslpDelegate({TARGET: 1008.54})
    weather = provider(
        delegate,
        Transport(payload()),
        Clock(OBSERVED + timedelta(hours=2, seconds=1)),
    )

    run = weather.resolve_run(requirement())
    weather.prepare_run(run.id, requirement())
    result = weather.query_batch(run.id, (request(),))[0]

    assert result.availability == Availability.AVAILABLE
    assert result.values["qnh_hpa"] == 1008.5
    assert result.values["qnh_method"] == "MSM_MSLP_ONLY"
    assert result.reason_code == "METAR_OBSERVATION_STALE"


def test_raw_metar_mismatch_falls_back_to_msm_mslp_only() -> None:
    delegate = MslpDelegate({TARGET: 1001.0})
    weather = provider(
        delegate,
        Transport(payload(raw_qnh=1009, altim=1008)),
        Clock(OBSERVED + timedelta(hours=1)),
    )

    run = weather.resolve_run(requirement())
    weather.prepare_run(run.id, requirement())
    result = weather.query_batch(run.id, (request(),))[0]

    assert result.values["qnh_method"] == "MSM_MSLP_ONLY"
    assert result.reason_code == "METAR_QNH_RAW_MISMATCH"


def test_msm_unavailable_requires_manual_qnh() -> None:
    delegate = MslpDelegate({OBSERVED: 1005.0, TARGET: None})
    weather = provider(delegate, Transport(payload()), Clock(OBSERVED + timedelta(hours=1)))

    run = weather.resolve_run(requirement())
    weather.prepare_run(run.id, requirement())
    result = weather.query_batch(run.id, (request(),))[0]

    assert result.availability == Availability.UNAVAILABLE
    assert result.values["qnh_method"] == "MANUAL"
    assert result.values["qnh_hpa"] is None
    assert "MANUAL_QNH_REQUIRED" in result.warnings


def test_fifty_parallel_queries_share_one_metar_http_request() -> None:
    delegate = MslpDelegate({OBSERVED: 1005.0, TARGET: 1008.5})
    transport = Transport(payload())
    weather = provider(delegate, transport, Clock(OBSERVED + timedelta(hours=1)))
    run = weather.resolve_run(requirement())
    weather.prepare_run(run.id, requirement())
    barrier = Barrier(51)

    def query(index: int) -> WeatherResult:
        barrier.wait()
        return weather.query_batch(
            run.id,
            (request().model_copy(update={"request_id": f"qnh-{index}"}),),
        )[0]

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(query, index) for index in range(50)]
        barrier.wait()
        results = [future.result(timeout=5) for future in futures]

    assert len(transport.calls) == 1
    assert all(result.values["qnh_hpa"] == 1011.5 for result in results)
