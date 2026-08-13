from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, RLock
from types import SimpleNamespace

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
from autonavlog.weather.msm_mslp_provider import MsmMslpWeatherProvider

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
        self.batches: list[tuple[WeatherRequest, ...]] = []

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
        self.batches.append(tuple(requests))
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
    assert result.warnings == ()
    assert prepared.metadata["terrain_required"] is False
    assert len(transport.calls) == 1
    assert "ids=RJFM,RJFO" in transport.calls[0]
    assert len(delegate.batches[-1]) == 2


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


def test_corrected_qnh_out_of_range_falls_back_to_msm_only() -> None:
    delegate = MslpDelegate({OBSERVED: 1090.0, TARGET: 850.0})
    weather = provider(delegate, Transport(payload()), Clock(OBSERVED + timedelta(hours=1)))
    run = weather.resolve_run(requirement())
    weather.prepare_run(run.id, requirement())

    result = weather.query_batch(run.id, (request(),))[0]

    assert result.values["qnh_hpa"] == 850.0
    assert result.values["qnh_method"] == "MSM_MSLP_ONLY"
    assert result.reason_code == "ESTIMATED_QNH_OUT_OF_RANGE"
    assert result.metadata["metar_fallback_reason"] == "ESTIMATED_QNH_OUT_OF_RANGE"


def test_unsupported_or_missing_station_skips_baseline_and_uses_msm_only() -> None:
    for metadata in ({"station_icao": "RJAA"}, {}):
        delegate = MslpDelegate({TARGET: 1003.0})
        weather = provider(delegate, Transport(payload()), Clock(OBSERVED + timedelta(hours=1)))
        run = weather.resolve_run(requirement())
        weather.prepare_run(run.id, requirement())
        unsupported = request().model_copy(update={"metadata": metadata})

        result = weather.query_batch(run.id, (unsupported,))[0]

        assert result.values["qnh_method"] == "MSM_MSLP_ONLY"
        assert result.reason_code == "METAR_STATION_UNSUPPORTED"
        assert len(delegate.batches[-1]) == 1


def test_aloft_delegation_preserves_request_identity_order_and_native_values() -> None:
    delegate = MslpDelegate({TARGET: 995.0})
    weather = provider(delegate, Transport(payload()), Clock(OBSERVED + timedelta(hours=3)))
    run = weather.resolve_run(requirement())
    weather.prepare_run(run.id, requirement())
    aloft = tuple(
        request().model_copy(
            update={
                "request_id": f"aloft-{index}",
                "kind": WeatherRequestKind.ALOFT,
                "altitude_ft_msl": 3000 + index * 500,
            }
        )
        for index in range(3)
    )

    results = weather.query_batch(run.id, aloft)

    assert [result.request_id for result in results] == [item.request_id for item in aloft]
    assert all(result.kind == WeatherRequestKind.ALOFT for result in results)
    assert all("label" not in result.values for result in results)


def test_msm_mslp_native_aloft_result_does_not_gain_qnh_labels_or_warnings() -> None:
    weather = MsmMslpWeatherProvider.__new__(MsmMslpWeatherProvider)
    weather._msm = SimpleNamespace(  # type: ignore[attr-defined]
        Availability=SimpleNamespace(AVAILABLE="available")
    )
    native = SimpleNamespace(
        availability="available",
        values={"wind_direction_deg": 270.0, "wind_speed_kt": 12.0},
        reason_code=None,
        warnings=("NATIVE_WARNING",),
        provenance={"fixture": True},
    )
    aloft_request = request().model_copy(
        update={
            "kind": WeatherRequestKind.ALOFT,
            "altitude_ft_msl": 3000,
        }
    )

    result = weather._result(aloft_request, native)

    assert "label" not in result.values
    assert "qnh_method" not in result.values
    assert result.warnings == ("NATIVE_WARNING",)


def test_msm_mslp_query_batch_allows_fifty_concurrent_calculations() -> None:
    weather = MsmMslpWeatherProvider.__new__(MsmMslpWeatherProvider)
    weather._lock = RLock()  # type: ignore[attr-defined]
    weather._prepared = {RUN_ID: object()}  # type: ignore[attr-defined]
    barrier = Barrier(50)

    def concurrent_query(prepared: object, item: WeatherRequest) -> WeatherResult:
        assert prepared is weather._prepared[RUN_ID]  # type: ignore[attr-defined]
        barrier.wait(timeout=5)
        return WeatherResult(
            request_id=item.request_id,
            availability=Availability.AVAILABLE,
            kind=item.kind,
            values={"mslp_hpa": 1000.0},
        )

    weather._query = concurrent_query  # type: ignore[method-assign]
    requests = tuple(
        request().model_copy(update={"request_id": f"parallel-{index}"}) for index in range(50)
    )

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [
            executor.submit(weather.query_batch, RUN_ID, (parallel_request,))
            for parallel_request in requests
        ]
        results = [future.result(timeout=10)[0] for future in futures]

    assert [result.request_id for result in results] == [
        parallel_request.request_id for parallel_request in requests
    ]


def test_msm_requirement_combination_is_sorted_and_deduplicated() -> None:
    middle = OBSERVED + timedelta(hours=1)
    later = OBSERVED + timedelta(hours=2)
    previous = ForecastRequirement(valid_times_utc=(OBSERVED, middle))
    current = ForecastRequirement(valid_times_utc=(middle, later))

    combined = MsmMslpWeatherProvider._combine(previous, current)

    assert combined.valid_times_utc == (OBSERVED, middle, later)
