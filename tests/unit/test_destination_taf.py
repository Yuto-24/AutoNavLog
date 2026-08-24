from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic

from autonavlog.domain.enums import Availability
from autonavlog.weather.destination_taf import AviationWeatherTafProvider

VALID_FROM = datetime(2026, 8, 13, tzinfo=UTC)
VALID_TO = VALID_FROM + timedelta(hours=30)


class Transport:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()
        self.calls: list[str] = []

    def __call__(self, url: str, _headers, _timeout_seconds: float) -> bytes:
        self.calls.append(url)
        return self.payload


def taf_payload() -> list[dict[str, object]]:
    return [
        {
            "icaoId": "RJFM",
            "mostRecent": 1,
            "issueTime": "2026-08-12T23:05:00.000Z",
            "validTimeFrom": VALID_FROM.timestamp(),
            "validTimeTo": VALID_TO.timestamp(),
            "rawTAF": (
                "TAF RJFM 122305Z 1300/1406 28006KT "
                "BECMG 1309/1311 20008KT"
            ),
            "fcsts": [
                {
                    "timeFrom": VALID_FROM.timestamp(),
                    "timeTo": VALID_TO.timestamp(),
                    "fcstChange": None,
                    "wdir": 280,
                    "wspd": 6,
                    "wgst": None,
                },
                {
                    "timeFrom": (VALID_FROM + timedelta(hours=9)).timestamp(),
                    "timeTo": (VALID_FROM + timedelta(hours=21)).timestamp(),
                    "fcstChange": "BECMG",
                    "wdir": 200,
                    "wspd": 8,
                    "wgst": 18,
                },
                {
                    "timeFrom": (VALID_FROM + timedelta(hours=10)).timestamp(),
                    "timeTo": (VALID_FROM + timedelta(hours=12)).timestamp(),
                    "fcstChange": "TEMPO",
                    "wdir": 320,
                    "wspd": 25,
                    "wgst": None,
                },
            ],
        }
    ]


def test_selects_prevailing_taf_wind_for_eta_and_caches_response() -> None:
    transport = Transport(taf_payload())
    provider = AviationWeatherTafProvider(
        transport=transport,
        clock=lambda: VALID_FROM,
    )

    forecast = provider.forecast("RJFM", VALID_FROM + timedelta(hours=10))
    second = provider.forecast("RJFM", VALID_FROM + timedelta(hours=11))

    assert forecast.availability == Availability.AVAILABLE
    assert forecast.wind_direction_deg_from == 200
    assert forecast.wind_speed_kt == 8
    assert forecast.wind_gust_kt == 18
    assert forecast.forecast_change == "BECMG"
    assert forecast.raw_taf is not None
    assert second.wind_direction_deg_from == 200
    assert len(transport.calls) == 1
    assert "ids=RJFM" in transport.calls[0]


def test_reports_out_of_range_without_using_an_unrelated_taf_period() -> None:
    provider = AviationWeatherTafProvider(
        transport=Transport(taf_payload()),
        clock=lambda: VALID_FROM,
    )

    forecast = provider.forecast("RJFM", VALID_TO + timedelta(minutes=1))

    assert forecast.availability == Availability.UNAVAILABLE
    assert forecast.reason_code == "TAF_TIME_OUT_OF_RANGE"


def test_transport_failure_does_not_raise_or_block_calculation() -> None:
    def failing_transport(_url: str, _headers, _timeout_seconds: float) -> bytes:
        raise OSError("offline")

    provider = AviationWeatherTafProvider(
        transport=failing_transport,
        clock=lambda: VALID_FROM,
    )

    forecast = provider.forecast("RJFO", VALID_FROM)

    assert forecast.availability == Availability.UNAVAILABLE
    assert forecast.reason_code == "TAF_FETCH_FAILED"


def test_transport_deadline_includes_blocked_name_resolution() -> None:
    release_transport = Event()
    calls: list[str] = []

    def blocking_transport(url: str, _headers, _timeout_seconds: float) -> bytes:
        calls.append(url)
        release_transport.wait()
        return b"[]"

    provider = AviationWeatherTafProvider(
        transport=blocking_transport,
        timeout_seconds=0.02,
        clock=lambda: VALID_FROM,
        maximum_concurrent_fetches=1,
    )
    started = monotonic()
    try:
        forecast = provider.forecast("RJFO", VALID_FROM)
        repeated = provider.forecast("RJFO", VALID_FROM)
        capacity_limited = provider.forecast("RJFM", VALID_FROM)
    finally:
        release_transport.set()
    elapsed = monotonic() - started

    assert elapsed < 0.5
    assert len(calls) == 1
    assert forecast.availability == Availability.UNAVAILABLE
    assert forecast.reason_code == "TAF_FETCH_TIMEOUT"
    assert repeated.reason_code == "TAF_FETCH_TIMEOUT"
    assert capacity_limited.reason_code == "TAF_FETCH_CAPACITY_UNAVAILABLE"


def test_blocked_transport_does_not_block_cached_records() -> None:
    release_transport = Event()

    def selective_transport(url: str, _headers, _timeout_seconds: float) -> bytes:
        if "ids=RJFO" in url:
            release_transport.wait()
            return b"[]"
        return json.dumps(taf_payload()).encode()

    provider = AviationWeatherTafProvider(
        transport=selective_transport,
        timeout_seconds=0.02,
        clock=lambda: VALID_FROM,
        maximum_concurrent_fetches=2,
    )
    cached = provider.forecast("RJFM", VALID_FROM)
    try:
        timed_out = provider.forecast("RJFO", VALID_FROM)
        started = monotonic()
        from_cache = provider.forecast("RJFM", VALID_FROM + timedelta(hours=1))
        elapsed = monotonic() - started
    finally:
        release_transport.set()

    assert timed_out.reason_code == "TAF_FETCH_TIMEOUT"
    assert cached.availability == Availability.AVAILABLE
    assert from_cache.availability == Availability.AVAILABLE
    assert elapsed < 0.5
