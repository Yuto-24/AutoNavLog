from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event

import pytest

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)
from autonavlog.weather.msm_metar_provider import (
    MANUAL_QNH_WARNING,
    METAR_QNH_LABEL,
    OBSERVED_QNH_WARNINGS,
    MsmMetarWeatherProvider,
)

OBSERVATION_TIME = datetime(2026, 7, 29, 18, tzinfo=timezone.utc)
RUN_ID = "20260729150000"


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _Transport:
    def __init__(self, *responses: bytes | Exception) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Mapping[str, str], float]] = []

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes:
        self.calls.append((url, dict(headers), timeout_seconds))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


class _RecordingDelegate:
    def __init__(self) -> None:
        self.resolved: list[ForecastRequirement] = []
        self.inspected: list[ForecastRequirement] = []
        self.prepared: list[ForecastRequirement] = []
        self.query_batches: list[tuple[WeatherRequest, ...]] = []
        self.last_results: tuple[WeatherResult, ...] = ()

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        self.resolved.append(requirement)
        return ForecastRun(id=RUN_ID, initial_time_utc=OBSERVATION_TIME - timedelta(hours=3))

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        self.inspected.append(requirement)
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=RUN_ID,
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
            metadata={"provider": "recording-msm"},
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        batch = tuple(requests)
        self.query_batches.append(batch)
        assert all(
            request.kind
            in {
                WeatherRequestKind.ALOFT,
                WeatherRequestKind.SURFACE_TEMPERATURE,
            }
            for request in batch
        )
        self.last_results = tuple(
            WeatherResult(
                request_id=request.request_id,
                availability=Availability.AVAILABLE,
                kind=request.kind,
                values={
                    "u_ms": 1.0,
                    "v_ms": 2.0,
                    "temperature_c": 8.0,
                },
                metadata={"delegate_marker": request.request_id},
            )
            for request in batch
        )
        return self.last_results


def _metar_record(
    *,
    station_icao: str = "RJFM",
    altim: object = 1008,
    raw_metar: str = "METAR RJFM 291800Z AUTO 28011KT 9999 NSC 29/23 Q1008",
    observation_time: object = OBSERVATION_TIME.timestamp(),
    report_time: object = "2026-07-29T18:00:00.000Z",
    receipt_time: object | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "icaoId": station_icao,
        "obsTime": observation_time,
        "reportTime": report_time,
        "altim": altim,
        "rawOb": raw_metar,
    }
    if receipt_time is not None:
        record["receiptTime"] = receipt_time
    return record


def _records_payload(*records: Mapping[str, object]) -> bytes:
    return json.dumps(records, separators=(",", ":")).encode()


def _metar_payload(**overrides: object) -> bytes:
    return _records_payload(_metar_record(**overrides))


def _requirement() -> ForecastRequirement:
    return ForecastRequirement(
        valid_times_utc=(OBSERVATION_TIME + timedelta(minutes=30),),
        require_aloft_wind=True,
        require_aloft_temperature=False,
        require_surface_temperature=True,
        require_estimated_qnh=True,
    )


def _qnh_request(
    request_id: str = "qnh",
    *,
    valid_time: datetime = OBSERVATION_TIME + timedelta(minutes=30),
    station_icao: str = "RJFM",
    metadata: dict[str, object] | None = None,
) -> WeatherRequest:
    return WeatherRequest(
        request_id=request_id,
        kind=WeatherRequestKind.ESTIMATED_QNH,
        latitude_deg=31.877,
        longitude_deg=131.448,
        valid_time_utc=valid_time,
        elevation_ft_msl=20,
        metadata={"station_icao": station_icao} if metadata is None else metadata,
    )


def _aloft_request(request_id: str = "aloft") -> WeatherRequest:
    return WeatherRequest(
        request_id=request_id,
        kind=WeatherRequestKind.ALOFT,
        latitude_deg=31.8,
        longitude_deg=131.4,
        valid_time_utc=OBSERVATION_TIME + timedelta(minutes=30),
        altitude_ft_msl=5000,
    )


def _surface_temperature_request(
    request_id: str = "departure:surface",
) -> WeatherRequest:
    return WeatherRequest(
        request_id=request_id,
        kind=WeatherRequestKind.SURFACE_TEMPERATURE,
        latitude_deg=31.877,
        longitude_deg=131.448,
        valid_time_utc=OBSERVATION_TIME + timedelta(minutes=30),
        elevation_ft_msl=20,
    )


def _prepared_provider(
    transport: _Transport,
    *,
    clock: _Clock | None = None,
    cache_ttl_seconds: float = 60,
    user_agent: str = "AutoNavLog-test/1.0",
) -> tuple[MsmMetarWeatherProvider, _RecordingDelegate]:
    delegate = _RecordingDelegate()
    provider = MsmMetarWeatherProvider(
        delegate,
        transport=transport,
        clock=clock or _Clock(OBSERVATION_TIME + timedelta(hours=1)),
        cache_ttl_seconds=cache_ttl_seconds,
        user_agent=user_agent,
    )
    provider.prepare_run(RUN_ID, _requirement())
    return provider, delegate


def test_lifecycle_removes_qnh_only_from_delegated_requirement() -> None:
    delegate = _RecordingDelegate()
    provider = MsmMetarWeatherProvider(
        delegate,
        transport=_Transport(_metar_payload()),
        clock=_Clock(OBSERVATION_TIME),
    )
    requirement = _requirement()

    resolved = provider.resolve_run(requirement)
    status = provider.inspect_run_status(resolved.id, requirement)
    prepared = provider.prepare_run(resolved.id, requirement)

    assert status.selected_run_covers_requirement
    delegated_requirements = delegate.resolved + delegate.inspected + delegate.prepared
    assert len(delegated_requirements) == 3
    assert all(not item.require_estimated_qnh for item in delegated_requirements)
    assert all(item.require_aloft_wind for item in delegated_requirements)
    assert all(not item.require_aloft_temperature for item in delegated_requirements)
    assert all(item.require_surface_temperature for item in delegated_requirements)
    assert prepared.requirement == requirement
    assert prepared.metadata["qnh_label"] == METAR_QNH_LABEL
    assert prepared.metadata["delegate"] == {"provider": "recording-msm"}


def test_mixed_batch_preserves_order_and_delegate_aloft_result() -> None:
    payload = _metar_payload()
    transport = _Transport(payload)
    provider, delegate = _prepared_provider(transport)
    requests = (
        _qnh_request("qnh-1"),
        _aloft_request(),
        _surface_temperature_request(),
        _qnh_request(
            "qnh-2",
            valid_time=OBSERVATION_TIME + timedelta(hours=1),
        ),
    )

    results = tuple(provider.query_batch(RUN_ID, requests))

    assert [result.request_id for result in results] == [
        "qnh-1",
        "aloft",
        "departure:surface",
        "qnh-2",
    ]
    assert delegate.query_batches == [(requests[1], requests[2])]
    assert results[1] is delegate.last_results[0]
    assert results[2] is delegate.last_results[1]
    assert len(transport.calls) == 1
    url, headers, timeout_seconds = transport.calls[0]
    assert url == "https://aviationweather.gov/api/data/metar?ids=RJFM&format=json"
    assert headers["User-Agent"] == "AutoNavLog-test/1.0"
    assert headers["Accept"] == "application/json"
    assert timeout_seconds == 10

    qnh = results[0]
    assert qnh.availability == Availability.AVAILABLE
    assert qnh.values == {"label": METAR_QNH_LABEL, "qnh_hpa": 1008.0}
    assert qnh.warnings == OBSERVED_QNH_WARNINGS
    provenance = qnh.metadata["provenance"]
    assert provenance["response_sha256"] == hashlib.sha256(payload).hexdigest()
    assert provenance["observation_time_utc"] == "2026-07-29T18:00:00+00:00"
    assert provenance["report_time_utc"] == "2026-07-29T18:00:00+00:00"
    assert provenance["raw_metar"].endswith("Q1008")
    assert provenance["observation_age_minutes"] == 60
    assert provenance["valid_time_offset_minutes"] == 30


def test_uncached_stations_in_one_batch_use_one_grouped_http_request() -> None:
    payload = _records_payload(
        _metar_record(),
        _metar_record(
            station_icao="RJFO",
            altim=1007,
            raw_metar="METAR RJFO 291800Z AUTO 25008KT 9999 Q1007",
        ),
    )
    transport = _Transport(payload)
    provider, _ = _prepared_provider(transport)

    results = provider.query_batch(
        RUN_ID,
        [
            _qnh_request("miyazaki", station_icao="RJFM"),
            _qnh_request("oita", station_icao="RJFO"),
            _qnh_request("miyazaki-duplicate", station_icao="RJFM"),
        ],
    )

    assert len(transport.calls) == 1
    assert (
        transport.calls[0][0]
        == "https://aviationweather.gov/api/data/metar?ids=RJFM,RJFO&format=json"
    )
    assert [result.values["qnh_hpa"] for result in results] == [1008, 1007, 1008]
    assert all(result.availability == Availability.AVAILABLE for result in results)


def test_unknown_station_in_later_batch_is_locally_rate_limited() -> None:
    clock = _Clock(OBSERVATION_TIME + timedelta(hours=1))
    transport = _Transport(
        _metar_payload(),
        _metar_payload(
            station_icao="RJFO",
            altim=1007,
            raw_metar="METAR RJFO 291800Z AUTO 25008KT 9999 Q1007",
        ),
    )
    provider, _ = _prepared_provider(transport, clock=clock)

    first = provider.query_batch(RUN_ID, [_qnh_request("first")])[0]
    clock.now += timedelta(seconds=30)
    cached, unknown = provider.query_batch(
        RUN_ID,
        [
            _qnh_request("cached", station_icao="RJFM"),
            _qnh_request("unknown", station_icao="RJFO"),
        ],
    )

    assert first.availability == Availability.AVAILABLE
    assert cached.availability == Availability.AVAILABLE
    assert unknown.availability == Availability.UNAVAILABLE
    assert unknown.reason_code == "METAR_LOCAL_RATE_LIMIT"
    assert unknown.metadata["provenance"]["retry_after_seconds"] == 30
    assert len(transport.calls) == 1

    clock.now += timedelta(seconds=30)
    retried = provider.query_batch(
        RUN_ID,
        [_qnh_request("retried", station_icao="RJFO")],
    )[0]

    assert retried.availability == Availability.AVAILABLE
    assert retried.values["qnh_hpa"] == 1007
    assert len(transport.calls) == 2


def test_parallel_queries_share_one_http_request_under_lock() -> None:
    transport = _Transport(_metar_payload())
    provider, _ = _prepared_provider(transport)
    barrier = Barrier(3)

    def query() -> WeatherResult:
        barrier.wait()
        return provider.query_batch(RUN_ID, [_qnh_request()])[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(query) for _ in range(2)]
        barrier.wait()
        results = [future.result() for future in futures]

    assert len(transport.calls) == 1
    assert all(result.availability == Availability.AVAILABLE for result in results)


def test_blocked_http_fetch_does_not_hold_cache_lock() -> None:
    class BlockingTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, Mapping[str, str], float]] = []
            self.second_started = Event()
            self.release_second = Event()

        def __call__(
            self,
            url: str,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> bytes:
            self.calls.append((url, dict(headers), timeout_seconds))
            if len(self.calls) == 1:
                return _metar_payload()
            if len(self.calls) == 2:
                self.second_started.set()
                if not self.release_second.wait(timeout=2):
                    raise TimeoutError("test transport was not released")
                return _metar_payload(
                    station_icao="RJFO",
                    altim=1007,
                    raw_metar="METAR RJFO 291800Z AUTO 25008KT 9999 Q1007",
                )
            raise AssertionError("unexpected transport call")

    clock = _Clock(OBSERVATION_TIME + timedelta(hours=1))
    transport = BlockingTransport()
    provider, _ = _prepared_provider(
        transport,  # type: ignore[arg-type]
        clock=clock,
        cache_ttl_seconds=120,
    )
    first = provider.query_batch(
        RUN_ID,
        [_qnh_request("first", station_icao="RJFM")],
    )[0]
    assert first.availability == Availability.AVAILABLE
    clock.now += timedelta(seconds=60)

    with ThreadPoolExecutor(max_workers=2) as executor:
        fetch_future = executor.submit(
            provider.query_batch,
            RUN_ID,
            [_qnh_request("new", station_icao="RJFO")],
        )
        assert transport.second_started.wait(timeout=1)
        cached_future = executor.submit(
            provider.query_batch,
            RUN_ID,
            [_qnh_request("cached", station_icao="RJFM")],
        )
        try:
            cached = cached_future.result(timeout=1)[0]
        finally:
            transport.release_second.set()
        fetched = fetch_future.result(timeout=2)[0]

    assert cached.availability == Availability.AVAILABLE
    assert fetched.availability == Availability.AVAILABLE
    assert len(transport.calls) == 2


def test_missing_station_fails_closed_without_transport_call() -> None:
    transport = _Transport(_metar_payload())
    provider, delegate = _prepared_provider(transport)

    result = provider.query_batch(
        RUN_ID,
        [_qnh_request(metadata={})],
    )[0]

    assert result.availability == Availability.UNAVAILABLE
    assert result.reason_code == "METAR_STATION_REQUIRED"
    assert result.values["qnh_hpa"] is None
    assert MANUAL_QNH_WARNING in result.warnings
    assert not transport.calls
    assert not delegate.query_batches


def test_alphanumeric_four_character_station_is_supported() -> None:
    provider, _ = _prepared_provider(
        _Transport(
            _metar_payload(
                station_icao="RJF1",
                raw_metar="METAR RJF1 291800Z AUTO 28011KT 9999 Q1008",
            )
        )
    )

    result = provider.query_batch(
        RUN_ID,
        [_qnh_request(station_icao="rjf1")],
    )[0]

    assert result.availability == Availability.AVAILABLE
    assert result.metadata["provenance"]["station_icao"] == "RJF1"


def test_http_204_maps_to_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoDataResponse:
        def __enter__(self) -> NoDataResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        @staticmethod
        def getcode() -> int:
            return 204

    monkeypatch.setattr(
        "autonavlog.weather.msm_metar_provider.urlopen",
        lambda request, timeout: NoDataResponse(),
    )
    delegate = _RecordingDelegate()
    provider = MsmMetarWeatherProvider(
        delegate,
        clock=_Clock(OBSERVATION_TIME + timedelta(hours=1)),
    )
    provider.prepare_run(RUN_ID, _requirement())

    result = provider.query_batch(RUN_ID, [_qnh_request()])[0]

    assert result.availability == Availability.UNAVAILABLE
    assert result.reason_code == "METAR_NO_DATA"
    assert result.metadata["provenance"]["http_status"] == 204


@pytest.mark.parametrize(
    ("now", "valid_time"),
    [
        (
            OBSERVATION_TIME + timedelta(hours=2, seconds=1),
            OBSERVATION_TIME + timedelta(minutes=30),
        ),
        (
            OBSERVATION_TIME + timedelta(minutes=30),
            OBSERVATION_TIME - timedelta(hours=2, seconds=1),
        ),
    ],
)
def test_observation_more_than_two_hours_away_fails_closed(
    now: datetime,
    valid_time: datetime,
) -> None:
    provider, _ = _prepared_provider(
        _Transport(_metar_payload()),
        clock=_Clock(now),
    )

    result = provider.query_batch(
        RUN_ID,
        [_qnh_request(valid_time=valid_time)],
    )[0]

    assert result.availability == Availability.UNAVAILABLE
    assert result.reason_code == "METAR_OBSERVATION_STALE"
    assert result.values["qnh_hpa"] is None
    assert MANUAL_QNH_WARNING in result.warnings


def test_exact_two_hour_offsets_are_allowed() -> None:
    provider, _ = _prepared_provider(
        _Transport(_metar_payload()),
        clock=_Clock(OBSERVATION_TIME + timedelta(hours=2)),
    )

    result = provider.query_batch(
        RUN_ID,
        [_qnh_request(valid_time=OBSERVATION_TIME - timedelta(hours=2))],
    )[0]

    assert result.availability == Availability.AVAILABLE
    assert result.values["qnh_hpa"] == 1008


@pytest.mark.parametrize(
    ("future_offset", "availability", "reason_code"),
    [
        (timedelta(minutes=5), Availability.AVAILABLE, None),
        (
            timedelta(minutes=5, seconds=1),
            Availability.UNAVAILABLE,
            "METAR_OBSERVATION_FROM_FUTURE",
        ),
    ],
)
def test_future_observation_allows_only_five_minutes_of_clock_skew(
    future_offset: timedelta,
    availability: Availability,
    reason_code: str | None,
) -> None:
    observation_time = OBSERVATION_TIME + future_offset
    provider, _ = _prepared_provider(
        _Transport(
            _metar_payload(
                observation_time=observation_time.timestamp(),
                report_time=observation_time.isoformat(),
            )
        ),
        clock=_Clock(OBSERVATION_TIME),
    )

    result = provider.query_batch(RUN_ID, [_qnh_request()])[0]

    assert result.availability == availability
    assert result.reason_code == reason_code
    if availability == Availability.UNAVAILABLE:
        assert result.values["qnh_hpa"] is None
        assert MANUAL_QNH_WARNING in result.warnings


@pytest.mark.parametrize(
    ("payload", "reason_code"),
    [
        (_metar_payload(station_icao="RJFK"), "METAR_STATION_MISMATCH"),
        (
            _metar_payload(
                altim=1200,
                raw_metar="METAR RJFM 291800Z AUTO 28011KT Q1200",
            ),
            "METAR_QNH_OUT_OF_RANGE",
        ),
        (
            _metar_payload(
                altim=1008,
                raw_metar="METAR RJFM 291800Z AUTO 28011KT Q1009",
            ),
            "METAR_QNH_RAW_MISMATCH",
        ),
        (
            _metar_payload(raw_metar="METAR RJFM 291800Z AUTO 28011KT"),
            "METAR_QNH_RAW_MISMATCH",
        ),
        (
            _metar_payload(observation_time="not-a-time"),
            "METAR_TIMESTAMP_INVALID",
        ),
        (b"[]", "METAR_NO_DATA"),
        (b"{}", "METAR_RESPONSE_INVALID"),
    ],
)
def test_invalid_metar_payloads_fail_closed(payload: bytes, reason_code: str) -> None:
    provider, _ = _prepared_provider(_Transport(payload))

    result = provider.query_batch(RUN_ID, [_qnh_request()])[0]

    assert result.availability == Availability.UNAVAILABLE
    assert result.reason_code == reason_code
    assert result.values["qnh_hpa"] is None
    assert result.warnings == (*OBSERVED_QNH_WARNINGS, MANUAL_QNH_WARNING)


def test_invalid_latest_record_never_falls_back_to_older_valid_qnh() -> None:
    payload = _records_payload(
        _metar_record(),
        _metar_record(
            observation_time=(OBSERVATION_TIME + timedelta(minutes=30)).timestamp(),
            report_time="2026-07-29T18:30:00Z",
            receipt_time="2026-07-29T18:31:00Z",
            altim=1008,
            raw_metar="METAR RJFM 291830Z AUTO 28011KT 9999 Q1009",
        ),
    )
    provider, _ = _prepared_provider(_Transport(payload))

    result = provider.query_batch(RUN_ID, [_qnh_request()])[0]

    assert result.availability == Availability.UNAVAILABLE
    assert result.reason_code == "METAR_QNH_RAW_MISMATCH"
    assert result.values["qnh_hpa"] is None


def test_failures_are_cached_for_sixty_seconds_then_retried() -> None:
    clock = _Clock(OBSERVATION_TIME + timedelta(hours=1))
    transport = _Transport(OSError("network down"), _metar_payload())
    provider, _ = _prepared_provider(transport, clock=clock)

    first = provider.query_batch(
        RUN_ID,
        [_qnh_request("first"), _qnh_request("second")],
    )
    clock.now += timedelta(seconds=59)
    cached = provider.query_batch(RUN_ID, [_qnh_request("cached")])

    assert all(result.reason_code == "METAR_FETCH_FAILED" for result in (*first, *cached))
    assert len(transport.calls) == 1

    clock.now += timedelta(seconds=1)
    retried = provider.query_batch(RUN_ID, [_qnh_request("retried")])[0]

    assert len(transport.calls) == 2
    assert retried.availability == Availability.AVAILABLE


def test_rejects_cache_ttl_below_api_minimum() -> None:
    with pytest.raises(ValueError, match="at least 60"):
        MsmMetarWeatherProvider(
            _RecordingDelegate(),
            transport=_Transport(_metar_payload()),
            cache_ttl_seconds=59.9,
        )
