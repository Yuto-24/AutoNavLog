from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autonavlog.domain.enums import Availability
from autonavlog.version import __version__

TAF_API_ENDPOINT = "https://aviationweather.gov/api/data/taf"
TAF_SOURCE_LABEL = "TAF（AviationWeather.gov）"

_ICAO_PATTERN = re.compile(r"[A-Z0-9]{4}")
_MAX_RESPONSE_BYTES = 1024 * 1024


class _PendingTafFetch:
    """Track one shared destination TAF fetch."""

    def __init__(self) -> None:
        self.completed = threading.Event()
        self.records: list[dict[str, Any]] | None = None
        self.error: Exception | None = None
        self.timed_out = False


class _TafFetchCapacityUnavailable(RuntimeError):
    """Report that the bounded TAF fetch capacity is exhausted."""


class DestinationWindForecast(BaseModel):
    model_config = ConfigDict(extra="forbid")

    airport_icao: str
    valid_time_utc: datetime | None
    availability: Availability
    wind_direction_deg_from: int | None = Field(default=None, ge=0, le=360)
    wind_speed_kt: int | None = Field(default=None, ge=0)
    wind_gust_kt: int | None = Field(default=None, ge=0)
    variable_direction: bool = False
    source_label: str = TAF_SOURCE_LABEL
    issue_time_utc: datetime | None = None
    taf_valid_from_utc: datetime | None = None
    taf_valid_to_utc: datetime | None = None
    forecast_change: str | None = None
    raw_taf: str | None = None
    reason_code: str | None = None

    @field_validator(
        "valid_time_utc",
        "issue_time_utc",
        "taf_valid_from_utc",
        "taf_valid_to_utc",
    )
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("destination wind times must be timezone-aware")
        return value.astimezone(UTC)


class DestinationWindProvider(Protocol):
    def forecast(
        self,
        airport_icao: str,
        valid_time_utc: datetime,
    ) -> DestinationWindForecast: ...


class FakeDestinationWindProvider:
    """Return deterministic destination wind for development and browser tests."""

    def forecast(
        self,
        airport_icao: str,
        valid_time_utc: datetime,
    ) -> DestinationWindForecast:
        return DestinationWindForecast(
            airport_icao=airport_icao.strip().upper(),
            valid_time_utc=valid_time_utc,
            availability=Availability.AVAILABLE,
            wind_direction_deg_from=200,
            wind_speed_kt=8,
            source_label="開発用固定TAF",
            forecast_change="BASE",
        )


class TafTransport(Protocol):
    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes: ...


def unavailable_destination_wind(
    airport_icao: str,
    valid_time_utc: datetime | None,
    reason_code: str,
) -> DestinationWindForecast:
    return DestinationWindForecast(
        airport_icao=airport_icao,
        valid_time_utc=valid_time_utc,
        availability=Availability.UNAVAILABLE,
        reason_code=reason_code,
    )


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> bytes:
    request = Request(url, headers=dict(headers))
    with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        payload = bytes(response.read(_MAX_RESPONSE_BYTES + 1))
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise ValueError("TAF response exceeds the size limit")
    return payload


class AviationWeatherTafProvider:
    """Fetch the prevailing destination wind from a decoded TAF."""

    def __init__(
        self,
        *,
        transport: TafTransport = _default_transport,
        timeout_seconds: float = 10.0,
        cache_ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] | None = None,
        maximum_concurrent_fetches: int = 4,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if maximum_concurrent_fetches < 1:
            raise ValueError("maximum_concurrent_fetches must be positive")
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._cache_ttl = cache_ttl
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
        self._inflight: dict[str, _PendingTafFetch] = {}
        self._fetch_slots = threading.BoundedSemaphore(maximum_concurrent_fetches)
        self._lock = threading.Lock()

    def forecast(
        self,
        airport_icao: str,
        valid_time_utc: datetime,
    ) -> DestinationWindForecast:
        normalized_icao = airport_icao.strip().upper()
        normalized_time = valid_time_utc.astimezone(UTC)
        if not _ICAO_PATTERN.fullmatch(normalized_icao):
            return unavailable_destination_wind(
                normalized_icao,
                normalized_time,
                "DESTINATION_ICAO_INVALID",
            )
        try:
            records = self._records_with_deadline(normalized_icao)
            return self._select(records, normalized_icao, normalized_time)
        except TimeoutError:
            return unavailable_destination_wind(
                normalized_icao,
                normalized_time,
                "TAF_FETCH_TIMEOUT",
            )
        except _TafFetchCapacityUnavailable:
            return unavailable_destination_wind(
                normalized_icao,
                normalized_time,
                "TAF_FETCH_CAPACITY_UNAVAILABLE",
            )
        except Exception:
            return unavailable_destination_wind(
                normalized_icao,
                normalized_time,
                "TAF_FETCH_FAILED",
            )

    def _records_with_deadline(self, airport_icao: str) -> list[dict[str, Any]]:
        """Bound the complete fetch, including DNS resolution, by the timeout."""
        now = self._clock().astimezone(UTC)
        start_worker = False
        with self._lock:
            cached = self._cache.get(airport_icao)
            if cached is not None and now - cached[0] < self._cache_ttl:
                return cached[1]
            pending = self._inflight.get(airport_icao)
            if pending is not None and pending.timed_out:
                raise TimeoutError("TAF fetch deadline already exceeded")
            if pending is None:
                if not self._fetch_slots.acquire(blocking=False):
                    raise _TafFetchCapacityUnavailable
                pending = _PendingTafFetch()
                self._inflight[airport_icao] = pending
                start_worker = True

        if start_worker:
            worker = threading.Thread(
                target=self._complete_fetch,
                args=(airport_icao, pending),
                daemon=True,
            )
            try:
                worker.start()
            except Exception as error:
                pending.error = error
                with self._lock:
                    self._inflight.pop(airport_icao, None)
                self._fetch_slots.release()
                pending.completed.set()
                raise

        if not pending.completed.wait(self._timeout_seconds):
            with self._lock:
                pending.timed_out = True
            raise TimeoutError("TAF fetch deadline exceeded")
        if pending.error is not None:
            raise pending.error
        if pending.records is None:
            raise RuntimeError("TAF fetch ended without a result")
        return pending.records

    def _complete_fetch(
        self,
        airport_icao: str,
        pending: _PendingTafFetch,
    ) -> None:
        try:
            pending.records = self._download_records(airport_icao)
            completed_at = self._clock().astimezone(UTC)
        except Exception as error:
            pending.error = error
            completed_at = None
        with self._lock:
            if pending.records is not None and completed_at is not None:
                self._cache[airport_icao] = (completed_at, pending.records)
            if self._inflight.get(airport_icao) is pending:
                self._inflight.pop(airport_icao)
        self._fetch_slots.release()
        pending.completed.set()

    def _download_records(self, airport_icao: str) -> list[dict[str, Any]]:
        query = urlencode({"ids": airport_icao, "format": "json"})
        payload = self._transport(
            f"{TAF_API_ENDPOINT}?{query}",
            {
                "Accept": "application/json",
                "User-Agent": f"AutoNavLog/{__version__} destination-wind",
            },
            self._timeout_seconds,
        )
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, list):
            raise ValueError("TAF response must be a list")
        return [record for record in decoded if isinstance(record, dict)]

    @staticmethod
    def _select(
        records: list[dict[str, Any]],
        airport_icao: str,
        valid_time_utc: datetime,
    ) -> DestinationWindForecast:
        record = next(
            (
                item
                for item in records
                if str(item.get("icaoId", "")).upper() == airport_icao
                and item.get("mostRecent") == 1
            ),
            None,
        )
        if record is None:
            record = next(
                (
                    item
                    for item in records
                    if str(item.get("icaoId", "")).upper() == airport_icao
                ),
                None,
            )
        if record is None:
            return unavailable_destination_wind(
                airport_icao,
                valid_time_utc,
                "TAF_UNAVAILABLE",
            )

        target = valid_time_utc.timestamp()
        valid_from = _finite_number(record.get("validTimeFrom"))
        valid_to = _finite_number(record.get("validTimeTo"))
        if valid_from is None or valid_to is None or not valid_from <= target <= valid_to:
            return unavailable_destination_wind(
                airport_icao,
                valid_time_utc,
                "TAF_TIME_OUT_OF_RANGE",
            )

        forecasts = record.get("fcsts")
        candidates: list[dict[str, Any]] = []
        if isinstance(forecasts, list):
            for item in forecasts:
                if not isinstance(item, dict):
                    continue
                change = str(item.get("fcstChange") or "").upper()
                if change.startswith("TEMPO") or change.startswith("PROB"):
                    continue
                time_from = _finite_number(item.get("timeFrom"))
                time_to = _finite_number(item.get("timeTo"))
                if time_from is None or time_to is None:
                    continue
                if time_from <= target < time_to or target == valid_to == time_to:
                    candidates.append(item)
        if not candidates:
            return unavailable_destination_wind(
                airport_icao,
                valid_time_utc,
                "TAF_WIND_UNAVAILABLE",
            )

        forecast = candidates[-1]
        speed = _nonnegative_integer(forecast.get("wspd"))
        gust = _nonnegative_integer(forecast.get("wgst"))
        raw_direction = forecast.get("wdir")
        direction = _direction(raw_direction)
        variable = isinstance(raw_direction, str) and raw_direction.upper() == "VRB"
        if speed is None or (speed > 0 and direction is None and not variable):
            return unavailable_destination_wind(
                airport_icao,
                valid_time_utc,
                "TAF_WIND_UNAVAILABLE",
            )

        return DestinationWindForecast(
            airport_icao=airport_icao,
            valid_time_utc=valid_time_utc,
            availability=Availability.AVAILABLE,
            wind_direction_deg_from=direction,
            wind_speed_kt=speed,
            wind_gust_kt=gust,
            variable_direction=variable,
            issue_time_utc=_iso_datetime(record.get("issueTime")),
            taf_valid_from_utc=datetime.fromtimestamp(valid_from, UTC),
            taf_valid_to_utc=datetime.fromtimestamp(valid_to, UTC),
            forecast_change=str(forecast.get("fcstChange") or "BASE"),
            raw_taf=(
                str(record["rawTAF"])
                if isinstance(record.get("rawTAF"), str)
                else None
            ),
        )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonnegative_integer(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or number < 0:
        return None
    return int(round(number))


def _direction(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or not 0 <= number <= 360:
        return None
    return int(round(number))


def _iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
