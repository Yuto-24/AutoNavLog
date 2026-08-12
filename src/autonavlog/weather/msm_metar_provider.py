from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRequirement,
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)

from .provider import WeatherProvider

METAR_API_ENDPOINT = "https://aviationweather.gov/api/data/metar"
METAR_QNH_LABEL = "METAR観測QNH"
OBSERVED_QNH_WARNINGS = (
    "OBSERVED_QNH_NOT_FORECAST",
    "VERIFY_WITH_OFFICIAL_AERODROME_WEATHER",
)
MANUAL_QNH_WARNING = "MANUAL_QNH_REQUIRED"

_MAX_RESPONSE_BYTES = 1024 * 1024
_MIN_HTTP_REQUEST_INTERVAL = timedelta(minutes=1)
_MAXIMUM_FUTURE_CLOCK_SKEW = timedelta(minutes=5)
_MAXIMUM_OBSERVATION_OFFSET = timedelta(hours=2)
_RAW_QNH_PATTERN = re.compile(r"(?:^|\s)Q(\d{4})(?=\s|$)")
_STATION_PATTERN = re.compile(r"[A-Z0-9]{4}")


class MetarTransport(Protocol):
    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> bytes: ...


@dataclass(frozen=True)
class _MetarObservation:
    station_icao: str
    qnh_hpa: float
    raw_qnh_hpa: float
    observation_time_utc: datetime
    report_time_utc: datetime
    raw_metar: str
    api_url: str
    response_sha256: str
    retrieved_at_utc: datetime


@dataclass(frozen=True)
class _MetarLookup:
    observation: _MetarObservation | None
    reason_code: str | None
    provenance: dict[str, Any]


@dataclass(frozen=True)
class _CacheEntry:
    fetched_at_utc: datetime
    lookup: _MetarLookup


class _MetarDataError(ValueError):
    def __init__(
        self,
        reason_code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.details = {} if details is None else dict(details)


class _MetarNoDataError(RuntimeError):
    pass


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _urllib_transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> bytes:
    request = Request(url, headers=dict(headers), method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        status = response.getcode()
        if status == 204:
            raise _MetarNoDataError("METAR API returned HTTP 204")
        if status != 200:
            raise RuntimeError(f"METAR API returned HTTP {status}")
        payload = cast(bytes, response.read(_MAX_RESPONSE_BYTES + 1))
    if len(payload) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("METAR API response exceeded the size limit")
    return payload


def _parse_utc_timestamp(value: object) -> datetime:
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("timestamp is not finite")
        try:
            return datetime.fromtimestamp(numeric, tz=timezone.utc)
        except (OSError, OverflowError, ValueError) as error:
            raise ValueError("timestamp is outside the supported range") from error
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is missing")
    normalized = value.strip()
    if normalized.endswith(("Z", "z")):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError("timestamp is not valid ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc)


class MsmMetarWeatherProvider:
    """Use MSM for aloft weather and a recent METAR observation for QNH."""

    def __init__(
        self,
        delegate: WeatherProvider,
        *,
        transport: MetarTransport = _urllib_transport,
        clock: Callable[[], datetime] = _default_clock,
        timeout_seconds: float = 10.0,
        cache_ttl_seconds: float = 60.0,
        maximum_observation_offset: timedelta = timedelta(hours=2),
        user_agent: str = "AutoNavLog/0.1 (+https://github.com/Yuto-24/AutoNavLog)",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if cache_ttl_seconds < 60:
            raise ValueError("METAR cache_ttl_seconds must be at least 60")
        if not timedelta(0) < maximum_observation_offset <= _MAXIMUM_OBSERVATION_OFFSET:
            raise ValueError(
                "maximum_observation_offset must be positive and no greater than 2 hours"
            )
        if not user_agent.strip():
            raise ValueError("user_agent must not be empty")
        self._delegate = delegate
        self._transport = transport
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self._maximum_observation_offset = maximum_observation_offset
        self._headers = {
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        self._prepared: dict[str, ForecastRequirement] = {}
        self._cache: dict[str, _CacheEntry] = {}
        self._station_fetch_locks: dict[str, threading.Lock] = {}
        self._last_http_request_at_utc: datetime | None = None
        self._cache_lock = threading.Lock()

    @property
    def cache_ttl_seconds(self) -> float:
        """Return the shared METAR cache lifetime in seconds."""
        return self._cache_ttl.total_seconds()

    def current_time_utc(self) -> datetime:
        """Return the provider clock normalized to UTC."""
        return self._now_utc()

    def lookup_metars(
        self,
        station_icaos: Sequence[str],
        now: datetime,
    ) -> dict[str, _MetarLookup]:
        """Return cached or freshly fetched METAR lookups for the stations."""
        return self._lookup_metars(station_icaos, now)

    def observation_provenance(
        self,
        observation: _MetarObservation,
        observation_age: timedelta,
        valid_time_offset: timedelta,
        requested_valid_time: datetime,
    ) -> dict[str, Any]:
        """Build stable provenance metadata for an accepted observation."""
        return self._observation_provenance(
            observation,
            observation_age,
            valid_time_offset,
            requested_valid_time,
        )

    @staticmethod
    def _without_estimated_qnh(
        requirement: ForecastRequirement,
    ) -> ForecastRequirement:
        return ForecastRequirement(
            valid_times_utc=requirement.valid_times_utc,
            require_aloft_wind=requirement.require_aloft_wind,
            require_aloft_temperature=requirement.require_aloft_temperature,
            require_estimated_qnh=False,
        )

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        """Select a delegate run for aloft weather."""
        return self._delegate.resolve_run(self._without_estimated_qnh(requirement))

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        """Inspect the selected delegate run without requiring QNH data."""
        return self._delegate.inspect_run_status(
            selected_run_id,
            self._without_estimated_qnh(requirement),
        )

    def prepare_run(
        self,
        forecast_run_id: str,
        requirement: ForecastRequirement,
    ) -> PreparedForecastRun:
        """Prepare a delegate run and record the METAR QNH requirement."""
        delegated = self._delegate.prepare_run(
            forecast_run_id,
            self._without_estimated_qnh(requirement),
        )
        self._prepared[forecast_run_id] = requirement
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={
                "provider": "msm-metar",
                "aloft_provider": "MSM",
                "qnh_provider": "NOAA Aviation Weather Center METAR",
                "qnh_label": METAR_QNH_LABEL,
                "metar_api_endpoint": METAR_API_ENDPOINT,
                "delegate": delegated.metadata,
            },
        )

    def query_batch(
        self,
        forecast_run_id: str,
        requests: Sequence[WeatherRequest],
    ) -> Sequence[WeatherResult]:
        """Query aloft weather and recent METAR QNH in original request order."""
        if forecast_run_id not in self._prepared:
            raise RuntimeError("MSM/METAR forecast run must be prepared before querying")

        results: list[WeatherResult | None] = [None] * len(requests)
        aloft_requests = tuple(
            (index, request)
            for index, request in enumerate(requests)
            if request.kind == WeatherRequestKind.ALOFT
        )
        if aloft_requests:
            delegated_results = tuple(
                self._delegate.query_batch(
                    forecast_run_id,
                    tuple(request for _, request in aloft_requests),
                )
            )
            if len(delegated_results) != len(aloft_requests):
                raise RuntimeError("MSM delegate batch result count does not match request count")
            for (index, request), result in zip(
                aloft_requests,
                delegated_results,
                strict=True,
            ):
                if result.request_id != request.request_id or result.kind != request.kind:
                    raise RuntimeError("MSM delegate changed weather request identity or order")
                results[index] = result

        qnh_requests = tuple(
            (index, request)
            for index, request in enumerate(requests)
            if request.kind == WeatherRequestKind.ESTIMATED_QNH
        )
        if qnh_requests:
            now = self._now_utc()
            station_requests: list[tuple[int, WeatherRequest, str]] = []
            unique_stations: list[str] = []
            seen_stations: set[str] = set()
            for index, request in qnh_requests:
                station_icao = self._station_icao(request)
                if station_icao is None:
                    results[index] = self._station_required_result(request)
                    continue
                station_requests.append((index, request, station_icao))
                if station_icao not in seen_stations:
                    seen_stations.add(station_icao)
                    unique_stations.append(station_icao)
            lookups = self._lookup_metars(tuple(unique_stations), now)
            for index, request, station_icao in station_requests:
                results[index] = self._qnh_result(
                    request,
                    now,
                    lookups[station_icao],
                )

        if any(result is None for result in results):
            raise RuntimeError("unsupported weather request kind")
        return tuple(cast(WeatherResult, result) for result in results)

    def _now_utc(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("METAR clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _station_icao(request: WeatherRequest) -> str | None:
        station_value = request.metadata.get("station_icao")
        if not isinstance(station_value, str):
            return None
        station_icao = station_value.strip().upper()
        if _STATION_PATTERN.fullmatch(station_icao) is None:
            return None
        return station_icao

    def _station_required_result(self, request: WeatherRequest) -> WeatherResult:
        provenance: dict[str, Any] = {
            "source_label": METAR_QNH_LABEL,
            "api_url": METAR_API_ENDPOINT,
            "requested_valid_time_utc": request.valid_time_utc.isoformat(),
        }
        station_value = request.metadata.get("station_icao")
        if isinstance(station_value, str):
            provenance["requested_station_icao"] = station_value
        return self._unavailable_result(
            request,
            "METAR_STATION_REQUIRED",
            provenance,
        )

    def _qnh_result(
        self,
        request: WeatherRequest,
        now: datetime,
        lookup: _MetarLookup,
    ) -> WeatherResult:
        if lookup.observation is None:
            return self._unavailable_result(
                request,
                lookup.reason_code or "METAR_QNH_UNAVAILABLE",
                lookup.provenance,
            )

        observation = lookup.observation
        observation_age = now - observation.observation_time_utc
        valid_time_offset = request.valid_time_utc - observation.observation_time_utc
        provenance = self._observation_provenance(
            observation,
            observation_age,
            valid_time_offset,
            request.valid_time_utc,
        )
        if observation_age < -_MAXIMUM_FUTURE_CLOCK_SKEW:
            return self._unavailable_result(
                request,
                "METAR_OBSERVATION_FROM_FUTURE",
                provenance,
            )
        if (
            observation_age > self._maximum_observation_offset
            or abs(valid_time_offset) > self._maximum_observation_offset
        ):
            return self._unavailable_result(
                request,
                "METAR_OBSERVATION_STALE",
                provenance,
            )

        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={
                "label": METAR_QNH_LABEL,
                "qnh_hpa": observation.qnh_hpa,
            },
            warnings=OBSERVED_QNH_WARNINGS,
            metadata={
                "provider": "noaa-awc-metar",
                "provenance": provenance,
            },
        )

    def _lookup_metars(
        self,
        station_icaos: Sequence[str],
        now: datetime,
    ) -> dict[str, _MetarLookup]:
        with self._cache_lock:
            lookups: dict[str, _MetarLookup] = {}
            pending: list[str] = []
            for station_icao in station_icaos:
                cached = self._cache.get(station_icao)
                if cached is not None:
                    cache_age = now - cached.fetched_at_utc
                    if abs(cache_age) < self._cache_ttl:
                        lookups[station_icao] = cached.lookup
                        continue
                pending.append(station_icao)
            if not pending:
                return lookups
            fetch_locks = [
                self._station_fetch_locks.setdefault(station_icao, threading.Lock())
                for station_icao in sorted(set(pending))
            ]

        acquired: list[threading.Lock] = []
        try:
            for fetch_lock in fetch_locks:
                fetch_lock.acquire()
                acquired.append(fetch_lock)
            with self._cache_lock:
                pending = []
                for station_icao in station_icaos:
                    cached = self._cache.get(station_icao)
                    if cached is not None:
                        cache_age = now - cached.fetched_at_utc
                        if abs(cache_age) < self._cache_ttl:
                            lookups[station_icao] = cached.lookup
                            continue
                    if station_icao not in pending:
                        pending.append(station_icao)
                if not pending:
                    return lookups

                if self._last_http_request_at_utc is not None:
                    elapsed = now - self._last_http_request_at_utc
                    if elapsed < _MIN_HTTP_REQUEST_INTERVAL:
                        elapsed_seconds = max(0.0, elapsed.total_seconds())
                        retry_after_seconds = max(
                            0.0,
                            _MIN_HTTP_REQUEST_INTERVAL.total_seconds() - elapsed_seconds,
                        )
                        api_url = self._api_url(tuple(pending))
                        for station_icao in pending:
                            lookups[station_icao] = _MetarLookup(
                                observation=None,
                                reason_code="METAR_LOCAL_RATE_LIMIT",
                                provenance={
                                    "source_label": METAR_QNH_LABEL,
                                    "api_url": api_url,
                                    "station_icao": station_icao,
                                    "request_suppressed": True,
                                    "last_http_request_time_utc": (
                                        self._last_http_request_at_utc.isoformat()
                                    ),
                                    "retry_after_seconds": retry_after_seconds,
                                },
                            )
                        return lookups

                self._last_http_request_at_utc = now

            fetched = self._fetch_metars(tuple(pending), now)
            with self._cache_lock:
                for station_icao in pending:
                    lookup = fetched[station_icao]
                    self._cache[station_icao] = _CacheEntry(
                        fetched_at_utc=now,
                        lookup=lookup,
                    )
                    lookups[station_icao] = lookup
            return lookups
        finally:
            for fetch_lock in reversed(acquired):
                fetch_lock.release()

    @staticmethod
    def _api_url(station_icaos: Sequence[str]) -> str:
        station_list = ",".join(station_icaos)
        query = urlencode(
            (("ids", station_list), ("format", "json")),
            safe=",",
        )
        return f"{METAR_API_ENDPOINT}?{query}"

    def _fetch_metars(
        self,
        station_icaos: Sequence[str],
        now: datetime,
    ) -> dict[str, _MetarLookup]:
        api_url = self._api_url(station_icaos)
        try:
            payload = self._transport(
                api_url,
                self._headers,
                self._timeout_seconds,
            )
        except _MetarNoDataError:
            return {
                station_icao: _MetarLookup(
                    observation=None,
                    reason_code="METAR_NO_DATA",
                    provenance={
                        "source_label": METAR_QNH_LABEL,
                        "api_url": api_url,
                        "station_icao": station_icao,
                        "retrieval_time_utc": now.isoformat(),
                        "http_status": 204,
                    },
                )
                for station_icao in station_icaos
            }
        except Exception as error:
            return {
                station_icao: _MetarLookup(
                    observation=None,
                    reason_code="METAR_FETCH_FAILED",
                    provenance={
                        "source_label": METAR_QNH_LABEL,
                        "api_url": api_url,
                        "station_icao": station_icao,
                        "retrieval_time_utc": now.isoformat(),
                        "error_type": type(error).__name__,
                    },
                )
                for station_icao in station_icaos
            }

        response_sha256 = hashlib.sha256(payload).hexdigest()
        if not payload:
            return {
                station_icao: _MetarLookup(
                    observation=None,
                    reason_code="METAR_NO_DATA",
                    provenance={
                        "source_label": METAR_QNH_LABEL,
                        "api_url": api_url,
                        "station_icao": station_icao,
                        "response_sha256": response_sha256,
                        "retrieval_time_utc": now.isoformat(),
                    },
                )
                for station_icao in station_icaos
            }

        lookups: dict[str, _MetarLookup] = {}
        for station_icao in station_icaos:
            base_provenance: dict[str, Any] = {
                "source_label": METAR_QNH_LABEL,
                "api_url": api_url,
                "station_icao": station_icao,
                "response_sha256": response_sha256,
                "retrieval_time_utc": now.isoformat(),
            }
            try:
                observation = self._parse_payload(
                    payload,
                    station_icao=station_icao,
                    api_url=api_url,
                    response_sha256=response_sha256,
                    retrieved_at_utc=now,
                )
            except _MetarDataError as error:
                lookups[station_icao] = _MetarLookup(
                    observation=None,
                    reason_code=error.reason_code,
                    provenance={**base_provenance, **error.details},
                )
            else:
                lookups[station_icao] = _MetarLookup(
                    observation=observation,
                    reason_code=None,
                    provenance=base_provenance,
                )
        return lookups

    @staticmethod
    def _parse_payload(
        payload: bytes,
        *,
        station_icao: str,
        api_url: str,
        response_sha256: str,
        retrieved_at_utc: datetime,
    ) -> _MetarObservation:
        try:
            decoded: object = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _MetarDataError("METAR_RESPONSE_INVALID") from error
        if not isinstance(decoded, list):
            raise _MetarDataError("METAR_RESPONSE_INVALID")
        if not decoded:
            raise _MetarDataError("METAR_NO_DATA")

        records: list[dict[str, object]] = []
        returned_stations: list[str] = []
        for item in decoded:
            if not isinstance(item, dict):
                continue
            record = cast(dict[str, object], item)
            returned_station = record.get("icaoId")
            if isinstance(returned_station, str):
                returned_stations.append(returned_station)
            if returned_station == station_icao:
                records.append(record)
        if not records:
            if not returned_stations:
                raise _MetarDataError("METAR_RESPONSE_INVALID")
            raise _MetarDataError(
                "METAR_STATION_MISMATCH",
                {"returned_station_icaos": returned_stations},
            )

        ranked_records: list[tuple[datetime, datetime, int, dict[str, object]]] = []
        for index, record in enumerate(records):
            try:
                observation_time = _parse_utc_timestamp(record.get("obsTime"))
                receipt_value = record.get("receiptTime")
                tie_break_time = _parse_utc_timestamp(
                    record.get("reportTime") if receipt_value is None else receipt_value
                )
            except ValueError as error:
                raise _MetarDataError("METAR_TIMESTAMP_INVALID") from error
            ranked_records.append((observation_time, tie_break_time, index, record))
        latest_record = max(
            ranked_records,
            key=lambda ranked: (ranked[0], ranked[1], ranked[2]),
        )[3]
        return MsmMetarWeatherProvider._parse_record(
            latest_record,
            station_icao=station_icao,
            api_url=api_url,
            response_sha256=response_sha256,
            retrieved_at_utc=retrieved_at_utc,
        )

    @staticmethod
    def _parse_record(
        record: Mapping[str, object],
        *,
        station_icao: str,
        api_url: str,
        response_sha256: str,
        retrieved_at_utc: datetime,
    ) -> _MetarObservation:
        raw_altimeter = record.get("altim")
        if isinstance(raw_altimeter, bool) or not isinstance(raw_altimeter, (int, float)):
            raise _MetarDataError("METAR_QNH_UNAVAILABLE")
        qnh_hpa = float(raw_altimeter)
        if not math.isfinite(qnh_hpa):
            raise _MetarDataError("METAR_QNH_UNAVAILABLE")
        if not 800 < qnh_hpa < 1100:
            raise _MetarDataError(
                "METAR_QNH_OUT_OF_RANGE",
                {"reported_qnh_hpa": qnh_hpa},
            )

        raw_metar = record.get("rawOb")
        if not isinstance(raw_metar, str) or not raw_metar.strip():
            raise _MetarDataError("METAR_QNH_RAW_MISMATCH")
        raw_qnh_values = _RAW_QNH_PATTERN.findall(raw_metar)
        if len(raw_qnh_values) != 1:
            raise _MetarDataError(
                "METAR_QNH_RAW_MISMATCH",
                {"raw_metar": raw_metar},
            )
        raw_qnh_hpa = float(raw_qnh_values[0])
        if not math.isclose(raw_qnh_hpa, qnh_hpa, rel_tol=0.0, abs_tol=0.1):
            raise _MetarDataError(
                "METAR_QNH_RAW_MISMATCH",
                {
                    "raw_metar": raw_metar,
                    "raw_qnh_hpa": raw_qnh_hpa,
                    "reported_qnh_hpa": qnh_hpa,
                },
            )

        try:
            observation_time = _parse_utc_timestamp(record.get("obsTime"))
            report_time = _parse_utc_timestamp(record.get("reportTime"))
        except ValueError as error:
            raise _MetarDataError(
                "METAR_TIMESTAMP_INVALID",
                {"raw_metar": raw_metar},
            ) from error

        return _MetarObservation(
            station_icao=station_icao,
            qnh_hpa=qnh_hpa,
            raw_qnh_hpa=raw_qnh_hpa,
            observation_time_utc=observation_time,
            report_time_utc=report_time,
            raw_metar=raw_metar,
            api_url=api_url,
            response_sha256=response_sha256,
            retrieved_at_utc=retrieved_at_utc,
        )

    @staticmethod
    def _observation_provenance(
        observation: _MetarObservation,
        observation_age: timedelta,
        valid_time_offset: timedelta,
        requested_valid_time: datetime,
    ) -> dict[str, Any]:
        age_minutes = observation_age.total_seconds() / 60
        offset_minutes = valid_time_offset.total_seconds() / 60
        return {
            "source_label": METAR_QNH_LABEL,
            "api_url": observation.api_url,
            "response_sha256": observation.response_sha256,
            "station_icao": observation.station_icao,
            "observation_time_utc": observation.observation_time_utc.isoformat(),
            "report_time_utc": observation.report_time_utc.isoformat(),
            "retrieval_time_utc": observation.retrieved_at_utc.isoformat(),
            "requested_valid_time_utc": requested_valid_time.isoformat(),
            "raw_metar": observation.raw_metar,
            "raw_qnh_hpa": observation.raw_qnh_hpa,
            "observation_age_minutes": age_minutes,
            "observation_age_absolute_minutes": abs(age_minutes),
            "valid_time_offset_minutes": offset_minutes,
            "valid_time_offset_absolute_minutes": abs(offset_minutes),
        }

    @staticmethod
    def _unavailable_result(
        request: WeatherRequest,
        reason_code: str,
        provenance: Mapping[str, Any],
    ) -> WeatherResult:
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.UNAVAILABLE,
            kind=request.kind,
            values={
                "label": METAR_QNH_LABEL,
                "qnh_hpa": None,
            },
            reason_code=reason_code,
            warnings=(*OBSERVED_QNH_WARNINGS, MANUAL_QNH_WARNING),
            metadata={
                "provider": "noaa-awc-metar",
                "provenance": dict(provenance),
            },
        )
