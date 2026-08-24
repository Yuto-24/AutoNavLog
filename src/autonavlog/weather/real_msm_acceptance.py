from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest, WeatherResult
from autonavlog.weather.msm_adapter import MsmWeatherProvider
from autonavlog.weather.provider import WeatherProvider

PINNED_MSM_DISTRIBUTION = "jma-msm-wind"
PINNED_MSM_VERSION = "0.2.1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class RealMsmAcceptanceError(RuntimeError):
    """A release prerequisite or live MSM acceptance assertion failed."""


@dataclass(frozen=True)
class AcceptanceProbe:
    icao: str
    latitude_deg: float
    longitude_deg: float
    elevation_ft_msl: float


# These are acceptance probes, not the application's authoritative airport database.
ACCEPTANCE_PROBES = (
    AcceptanceProbe("RJFM", 31.877, 131.449, 20.0),
    AcceptanceProbe("RJFO", 33.479, 131.737, 19.0),
)


@dataclass(frozen=True)
class RealMsmAcceptanceConfig:
    valid_time_utc: datetime | None = None
    altitude_ft_msl: float = 5_000.0
    live: bool = False

    def __post_init__(self) -> None:
        if self.live and self.valid_time_utc is None:
            raise ValueError("live MSM acceptance requires valid_time_utc")
        if self.valid_time_utc is not None and self.valid_time_utc.tzinfo is None:
            raise ValueError("valid_time_utc must be timezone-aware")
        if not math.isfinite(self.altitude_ft_msl) or self.altitude_ft_msl <= 0:
            raise ValueError("altitude_ft_msl must be a positive finite number")


def _fail(message: str) -> NoReturn:
    raise RealMsmAcceptanceError(message)


def _as_finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        _fail(f"{label} must be numeric, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(f"{label} must be numeric")
    if not math.isfinite(number):
        _fail(f"{label} must be finite")
    return number


def _as_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value.lower()) is None:
        _fail(f"{label} must be a 64-character SHA-256")
    return value.lower()


def _provider_as_real_msm(provider: WeatherProvider) -> MsmWeatherProvider:
    # Deliberately use exact type identity: a FakeWeatherProvider (or a wrapper/subclass
    # around one) must never satisfy the real-MSM release gate.
    if type(provider) is not MsmWeatherProvider:
        _fail(
            "real MSM acceptance requires an exact MsmWeatherProvider instance; "
            f"received {type(provider).__module__}.{type(provider).__qualname__}"
        )
    return provider


def _validate_pinned_package(provider: MsmWeatherProvider) -> dict[str, str]:
    try:
        distribution_version = metadata.version(PINNED_MSM_DISTRIBUTION)
    except metadata.PackageNotFoundError:
        _fail(f"{PINNED_MSM_DISTRIBUTION} is not installed as a distribution")
    module_version = getattr(getattr(provider, "_msm", None), "__version__", None)
    adapter_version = getattr(provider, "package_version", None)
    versions = {
        "distribution": distribution_version,
        "module": module_version,
        "adapter": adapter_version,
    }
    mismatches = {label: value for label, value in versions.items() if value != PINNED_MSM_VERSION}
    if mismatches:
        _fail(
            f"real MSM acceptance requires {PINNED_MSM_DISTRIBUTION} "
            f"{PINNED_MSM_VERSION} exactly; observed {mismatches}"
        )
    return cast(dict[str, str], versions)


def _validate_source_provenance(
    result: WeatherResult,
    *,
    forecast_initial_time_utc: datetime,
) -> dict[str, Any]:
    provenance = result.metadata.get("provenance")
    if not isinstance(provenance, Mapping):
        _fail(f"{result.request_id}: provenance is absent or not an object")

    source_urls_value = provenance.get("source_urls")
    if not isinstance(source_urls_value, Sequence) or isinstance(source_urls_value, (str, bytes)):
        _fail(f"{result.request_id}: provenance source_urls is not a list")
    source_urls = tuple(source_urls_value)
    if not source_urls:
        _fail(f"{result.request_id}: provenance source_urls is empty")
    for source_url in source_urls:
        if not isinstance(source_url, str):
            _fail(f"{result.request_id}: provenance contains a non-string source URL")
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            _fail(f"{result.request_id}: invalid source URL: {source_url!r}")

    source_hashes_value = provenance.get("source_hashes")
    if not isinstance(source_hashes_value, Mapping) or not source_hashes_value:
        _fail(f"{result.request_id}: provenance source_hashes is absent or empty")
    source_hashes: dict[str, str] = {}
    for source_url, digest in source_hashes_value.items():
        if not isinstance(source_url, str):
            _fail(f"{result.request_id}: source_hashes contains a non-string URL key")
        source_hashes[source_url] = _as_sha256(
            digest,
            f"{result.request_id}: source hash for {source_url}",
        )
    if set(source_urls) != set(source_hashes):
        _fail(
            f"{result.request_id}: source URL/hash key mismatch "
            f"(urls={sorted(source_urls)}, hashes={sorted(source_hashes)})"
        )

    initial_time_value = provenance.get("initial_time_utc")
    if not isinstance(initial_time_value, str):
        _fail(f"{result.request_id}: provenance initial_time_utc is absent")
    try:
        initial_time = datetime.fromisoformat(initial_time_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RealMsmAcceptanceError(
            f"{result.request_id}: invalid provenance initial_time_utc"
        ) from error
    if initial_time.tzinfo is None:
        _fail(f"{result.request_id}: provenance initial_time_utc is timezone-naive")
    if initial_time.astimezone(UTC) != forecast_initial_time_utc.astimezone(UTC):
        _fail(f"{result.request_id}: provenance forecast run does not match resolved run")

    interpolation_method = provenance.get("interpolation_method")
    if not isinstance(interpolation_method, str) or not interpolation_method.strip():
        _fail(f"{result.request_id}: provenance interpolation_method is absent")
    trace = provenance.get("trace")
    if not isinstance(trace, Mapping) or not trace:
        _fail(f"{result.request_id}: provenance trace is absent or empty")
    return {
        "source_urls": list(source_urls),
        "source_hashes": source_hashes,
        "initial_time_utc": initial_time.astimezone(UTC).isoformat(),
        "interpolation_method": interpolation_method,
        "trace_keys": sorted(str(key) for key in trace),
    }


def _validate_weather_result(
    request: WeatherRequest,
    result: WeatherResult,
    *,
    forecast_initial_time_utc: datetime,
) -> dict[str, Any]:
    if result.request_id != request.request_id:
        _fail(
            f"weather response ID mismatch: expected {request.request_id}, "
            f"received {result.request_id}"
        )
    if result.kind != request.kind:
        _fail(f"{request.request_id}: weather response kind mismatch")
    if result.availability != Availability.AVAILABLE:
        _fail(
            f"{request.request_id}: real MSM result is not AVAILABLE (reason={result.reason_code})"
        )

    if request.kind == WeatherRequestKind.ALOFT:
        _as_finite_number(result.values.get("u_ms"), f"{request.request_id}: u_ms")
        _as_finite_number(result.values.get("v_ms"), f"{request.request_id}: v_ms")
        speed_kt = _as_finite_number(
            result.values.get("wind_speed_kt"),
            f"{request.request_id}: wind_speed_kt",
        )
        if speed_kt < 0:
            _fail(f"{request.request_id}: wind_speed_kt must be non-negative")
        _as_finite_number(
            result.values.get("temperature_c"),
            f"{request.request_id}: temperature_c",
        )
    elif request.kind == WeatherRequestKind.SURFACE_TEMPERATURE:
        temperature_k = _as_finite_number(
            result.values.get("temperature_k"),
            f"{request.request_id}: temperature_k",
        )
        temperature_c = _as_finite_number(
            result.values.get("temperature_c"),
            f"{request.request_id}: temperature_c",
        )
        if not 150 <= temperature_k <= 350:
            _fail(
                f"{request.request_id}: temperature_k is outside the accepted sanity range"
            )
        if not math.isclose(
            temperature_c,
            temperature_k - 273.15,
            rel_tol=0.0,
            abs_tol=1e-6,
        ):
            _fail(f"{request.request_id}: Kelvin/Celsius values are inconsistent")
        if result.metadata.get("source_variable") != "tmp_surface":
            _fail(f"{request.request_id}: MSM surface-temperature source is not tmp_surface")
    provenance = _validate_source_provenance(
        result,
        forecast_initial_time_utc=forecast_initial_time_utc,
    )
    return {
        "request_id": result.request_id,
        "kind": result.kind.value,
        "availability": result.availability.value,
        "provenance": provenance,
    }


def _weather_requests(
    valid_time_utc: datetime,
    altitude_ft_msl: float,
) -> tuple[WeatherRequest, ...]:
    requests: list[WeatherRequest] = []
    for probe in ACCEPTANCE_PROBES:
        requests.extend(
            (
                WeatherRequest(
                    request_id=f"{probe.icao}-aloft",
                    kind=WeatherRequestKind.ALOFT,
                    latitude_deg=probe.latitude_deg,
                    longitude_deg=probe.longitude_deg,
                    valid_time_utc=valid_time_utc,
                    altitude_ft_msl=altitude_ft_msl,
                ),
                WeatherRequest(
                    request_id=f"{probe.icao}-surface-temperature",
                    kind=WeatherRequestKind.SURFACE_TEMPERATURE,
                    latitude_deg=probe.latitude_deg,
                    longitude_deg=probe.longitude_deg,
                    valid_time_utc=valid_time_utc,
                    elevation_ft_msl=probe.elevation_ft_msl,
                ),
            )
        )
    return tuple(requests)


def run_real_msm_acceptance(
    provider: WeatherProvider,
    config: RealMsmAcceptanceConfig,
) -> dict[str, Any]:
    """Validate release artifacts and, only when requested, execute live MSM queries.

    Offline preflight is the default and validates the exact installed package.
    It is not evidence of live MSM success. Any missing or invalid
    prerequisite raises RealMsmAcceptanceError; the gate never converts that into SKIP.
    """

    msm_provider = _provider_as_real_msm(provider)
    package_versions = _validate_pinned_package(msm_provider)
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "PASS",
        "mode": "LIVE" if config.live else "OFFLINE_PREFLIGHT",
        "live_executed": config.live,
        "package": {
            "distribution": PINNED_MSM_DISTRIBUTION,
            "required_version": PINNED_MSM_VERSION,
            "observed_versions": package_versions,
        },
    }
    if not config.live:
        return report

    valid_time = config.valid_time_utc
    if valid_time is None:  # Guard for callers bypassing dataclass validation.
        _fail("live MSM acceptance requires valid_time_utc")
    valid_time = valid_time.astimezone(UTC)
    requirement = ForecastRequirement(
        valid_times_utc=(valid_time,),
        require_surface_temperature=True,
    )

    forecast_run = msm_provider.resolve_run(requirement)
    status = msm_provider.inspect_run_status(forecast_run.id, requirement)
    if status.selected_run_id != forecast_run.id:
        _fail("resolved MSM run cannot be reselected by its ID")
    if not status.selected_run_covers_requirement:
        _fail("resolved MSM run does not cover the acceptance requirement")

    prepared = msm_provider.prepare_run(forecast_run.id, requirement)
    if prepared.forecast_run_id != forecast_run.id:
        _fail("prepared MSM run ID does not match the resolved run")
    if prepared.metadata.get("provider") != "jma-msm-wind":
        _fail("prepared forecast metadata does not identify jma-msm-wind")
    if prepared.metadata.get("package_version") != PINNED_MSM_VERSION:
        _fail("prepared forecast metadata does not contain the pinned MSM version")

    requests = _weather_requests(valid_time, config.altitude_ft_msl)
    results = tuple(msm_provider.query_batch(forecast_run.id, requests))
    if len(results) != len(requests):
        _fail(f"real MSM batch returned {len(results)} result(s) for {len(requests)} request(s)")
    if len({result.request_id for result in results}) != len(results):
        _fail("real MSM batch contains duplicate response IDs")
    by_request_id = {result.request_id: result for result in results}
    expected_ids = {request.request_id for request in requests}
    if set(by_request_id) != expected_ids:
        _fail("real MSM batch response IDs do not match the requested IDs")

    checked_results = [
        _validate_weather_result(
            request,
            by_request_id[request.request_id],
            forecast_initial_time_utc=forecast_run.initial_time_utc,
        )
        for request in requests
    ]
    report["forecast"] = {
        "valid_time_utc": valid_time.isoformat(),
        "resolved_run_id": forecast_run.id,
        "initial_time_utc": forecast_run.initial_time_utc.astimezone(UTC).isoformat(),
        "result_count": len(checked_results),
        "results": checked_results,
    }
    return report
