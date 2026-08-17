from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from math import isfinite
from numbers import Integral, Real
from typing import Any

from autonavlog.domain.enums import Availability
from autonavlog.domain.weather import WeatherRequest, WeatherResult

# Deliberately wider than terrestrial operational weather, while still catching
# Celsius-as-K and corrupt-unit payloads before they reach the NAV LOG.
MIN_SURFACE_TEMPERATURE_K = 150.0
MAX_SURFACE_TEMPERATURE_K = 350.0


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value)
    return value


def msm_surface_temperature_result(
    prepared: Any,
    request: WeatherRequest,
) -> WeatherResult:
    """Sample normalized MSM Lsurf temperature and retain interpolation provenance."""

    metadata: dict[str, Any] = {
        "provider": "jma-msm-wind",
        "source_variable": "tmp_surface",
        "requested_valid_time_utc": request.valid_time_utc.isoformat(),
        "requested_elevation_ft_msl": request.elevation_ft_msl,
    }
    sampler = getattr(prepared, "_surface_scalar", None)
    if not callable(sampler):
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.UNAVAILABLE,
            kind=request.kind,
            values={"temperature_c": None},
            reason_code="SURFACE_TEMPERATURE_QUERY_UNSUPPORTED",
            metadata=metadata,
        )
    sampled = sampler(
        "tmp_surface",
        request.latitude_deg,
        request.longitude_deg,
        request.valid_time_utc,
    )
    if sampled is None:
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.UNAVAILABLE,
            kind=request.kind,
            values={"temperature_c": None},
            reason_code="SURFACE_TEMPERATURE_UNAVAILABLE",
            metadata=metadata,
        )
    raw_kelvin, trace = sampled
    try:
        temperature_k = float(raw_kelvin)
    except (TypeError, ValueError):
        temperature_k = float("nan")
    if not (
        isfinite(temperature_k)
        and MIN_SURFACE_TEMPERATURE_K
        <= temperature_k
        <= MAX_SURFACE_TEMPERATURE_K
    ):
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.UNAVAILABLE,
            kind=request.kind,
            values={"temperature_c": None},
            reason_code="SURFACE_TEMPERATURE_INVALID",
            metadata=metadata,
        )
    provenance = getattr(prepared, "_provenance", None)
    if callable(provenance):
        metadata["provenance"] = _jsonable(
            provenance("bilinear,time-linear", {"temperature": trace})
        )
    return WeatherResult(
        request_id=request.request_id,
        availability=Availability.AVAILABLE,
        kind=request.kind,
        values={
            "temperature_k": temperature_k,
            "temperature_c": temperature_k - 273.15,
        },
        warnings=("NOT_FOR_OPERATIONAL_USE",),
        metadata=metadata,
    )
