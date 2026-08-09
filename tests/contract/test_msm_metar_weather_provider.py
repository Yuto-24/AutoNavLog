from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import pytest

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest, WeatherResult
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.msm_metar_provider import MsmMetarWeatherProvider
from autonavlog.weather.provider import WeatherProvider

OBSERVATION_TIME = datetime(2026, 7, 29, 18, tzinfo=timezone.utc)
RUN_ID = "20260729150000"


def _transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> bytes:
    return json.dumps(
        [
            {
                "icaoId": "RJFM",
                "obsTime": OBSERVATION_TIME.timestamp(),
                "reportTime": "2026-07-29T18:00:00Z",
                "altim": 1008,
                "rawOb": "METAR RJFM 291800Z AUTO 28011KT 9999 Q1008",
            }
        ]
    ).encode()


def _requirement() -> ForecastRequirement:
    return ForecastRequirement(
        valid_times_utc=(OBSERVATION_TIME + timedelta(minutes=30),),
    )


def _qnh_request() -> WeatherRequest:
    return WeatherRequest(
        request_id="qnh",
        kind=WeatherRequestKind.ESTIMATED_QNH,
        latitude_deg=31.877,
        longitude_deg=131.448,
        valid_time_utc=OBSERVATION_TIME + timedelta(minutes=30),
        elevation_ft_msl=20,
        metadata={"station_icao": "RJFM"},
    )


def _aloft_request() -> WeatherRequest:
    return WeatherRequest(
        request_id="aloft",
        kind=WeatherRequestKind.ALOFT,
        latitude_deg=31.8,
        longitude_deg=131.4,
        valid_time_utc=OBSERVATION_TIME + timedelta(minutes=30),
        altitude_ft_msl=5000,
    )


def test_hybrid_provider_implements_contract_and_never_delegates_qnh() -> None:
    delegate = FakeWeatherProvider((RUN_ID,))
    provider = MsmMetarWeatherProvider(
        delegate,
        transport=_transport,
        clock=lambda: OBSERVATION_TIME + timedelta(hours=1),
    )
    requirement = _requirement()

    assert isinstance(provider, WeatherProvider)
    run = provider.resolve_run(requirement)
    prepared = provider.prepare_run(run.id, requirement)
    results = tuple(provider.query_batch(run.id, [_qnh_request(), _aloft_request()]))

    assert prepared.requirement == requirement
    assert delegate.prepared[run.id].require_estimated_qnh is False
    assert len(delegate.query_history) == 1
    assert delegate.query_history[0][1] == (_aloft_request(),)
    assert [result.request_id for result in results] == ["qnh", "aloft"]
    assert results[0].availability == Availability.AVAILABLE


def test_hybrid_provider_rejects_delegate_identity_or_order_changes() -> None:
    def wrong_identity(request: WeatherRequest) -> WeatherResult:
        return WeatherResult(
            request_id=f"wrong-{request.request_id}",
            availability=Availability.AVAILABLE,
            kind=request.kind,
        )

    delegate = FakeWeatherProvider((RUN_ID,), result_factory=wrong_identity)
    provider = MsmMetarWeatherProvider(
        delegate,
        transport=_transport,
        clock=lambda: OBSERVATION_TIME,
    )
    provider.prepare_run(RUN_ID, _requirement())

    with pytest.raises(RuntimeError, match="changed weather request identity or order"):
        provider.query_batch(RUN_ID, [_aloft_request()])
