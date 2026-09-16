from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from autonavlog.domain.planning import PatternAltitudeValidationStatus
from autonavlog.nav.geodesy import geodesic_leg
from autonavlog.performance.climb import ClimbCalculator, ClimbPerformanceError
from autonavlog.performance.cruise import (
    CruisePerformanceError,
    CruisePerformanceSelectionPolicy,
)
from autonavlog.performance.repository import (
    PerformanceDataError,
    PerformanceRepository,
)
from autonavlog.storage.reference_data import ReferenceDataCatalogRepository, ReferenceDataError

_SHA256 = re.compile(r"[0-9a-f]{64}")
_PENDING = {"", "PENDING", "UNVERIFIED", "UNKNOWN"}


class RuntimeDataValidationError(RuntimeError):
    """A release runtime-data prerequisite is absent or not source-verified."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeDataValidationError(message)


def _verified_text(value: str, label: str) -> None:
    _require(
        value.strip().upper() not in _PENDING,
        f"{label} is missing or pending",
    )


def validate_runtime_data(
    data_root: str | Path,
    *,
    required_airport_ids: tuple[str, ...] = ("RJFM", "RJFO"),
    nominal_altitude_ft: float = 5_000.0,
    nominal_temperature_c: float = 15.0,
) -> dict[str, Any]:
    """Validate the minimum source-backed data needed by the NavMate Web release."""

    root = Path(data_root)
    reference_path = root / "reference" / "default"
    performance_path = root / "performance"
    _require(reference_path.is_dir(), f"reference pack is absent: {reference_path}")
    _require(
        performance_path.is_dir(),
        f"performance directory is absent: {performance_path}",
    )

    try:
        reference_catalog = ReferenceDataCatalogRepository(
            root / ".reference-validation"
        ).open_pack(reference_path)
    except (OSError, ReferenceDataError, ValueError) as error:
        raise RuntimeDataValidationError(f"reference pack is invalid: {error}") from error
    all_airports = list(reference_catalog.airports.values())
    _require(bool(all_airports), "reference pack has no airport rows")
    required_airports = []
    for airport_id in required_airport_ids:
        try:
            airport = reference_catalog.airports[airport_id]
        except KeyError as error:
            raise RuntimeDataValidationError(f"required airport is absent: {airport_id}") from error
        _verified_text(airport.source, f"{airport_id} source")
        _verified_text(airport.source_revision, f"{airport_id} source_revision")
        _verified_text(
            airport.pattern_altitude_source,
            f"{airport_id} pattern_altitude_source",
        )
        _verified_text(
            airport.pattern_altitude_source_revision,
            f"{airport_id} pattern_altitude_source_revision",
        )
        _require(
            airport.pattern_altitude_validation_status == PatternAltitudeValidationStatus.VERIFIED,
            f"{airport_id} pattern altitude source verification is not VERIFIED",
        )
        required_airports.append(airport)
    _require(
        len({(airport.latitude_deg, airport.longitude_deg) for airport in required_airports})
        == len(required_airports),
        "required airports must have distinct coordinates",
    )

    try:
        performance = PerformanceRepository.from_directory(performance_path)
        performance.require_verified()
    except (OSError, PerformanceDataError, ValueError) as error:
        raise RuntimeDataValidationError(f"performance data is invalid: {error}") from error
    manifest = performance.manifest
    _require(manifest.aircraft == "SR22 G6", "performance aircraft must be SR22 G6")
    _verified_text(manifest.source_document, "performance source_document")
    _verified_text(manifest.source_revision, "performance source_revision")
    _verified_text(manifest.verified_against, "performance verified_against")
    for table in manifest.tables:
        _verified_text(table.source_page, f"{table.id} source_page")
        _require(
            isinstance(table.sha256, str) and _SHA256.fullmatch(table.sha256.lower()) is not None,
            f"{table.id} must declare a 64-character SHA-256",
        )

    weights = sorted({row.weight_lb for row in performance.climb_rows})
    _require(bool(weights), "climb table contains no aircraft weight axis")
    try:
        nominal_climb = ClimbCalculator(
            performance.climb_rows,
            manifest.climb_temperature_policy,
        ).calculate(
            required_airports[0].elevation_ft_msl,
            nominal_altitude_ft,
            nominal_temperature_c,
            weights[0],
        )
        nominal_cruise = CruisePerformanceSelectionPolicy(performance.cruise_rows).select(
            pressure_altitude_ft=nominal_altitude_ft,
            isa_deviation_c=0.0,
            distance_nm=100.0,
            true_course_deg=0.0,
            wind_direction_deg_from=None,
            wind_speed_kt=0.0,
        )
    except (ClimbPerformanceError, CruisePerformanceError) as error:
        raise RuntimeDataValidationError(
            f"performance nominal-condition probe failed: {error}"
        ) from error
    route_probe = geodesic_leg(
        required_airports[0].latitude_deg,
        required_airports[0].longitude_deg,
        required_airports[-1].latitude_deg,
        required_airports[-1].longitude_deg,
    )
    _require(route_probe.distance_nm > 0, "required-airport route probe is empty")

    return {
        "schema_version": 1,
        "status": "PASS",
        "data_root": str(root.resolve()),
        "reference_data": {
            "dataset_id": reference_catalog.manifest.dataset_id,
            "revision": reference_catalog.manifest.revision,
            "airport_row_count": len(all_airports),
        },
        "airports": {
            "row_count": len(all_airports),
            "required": [airport.id for airport in required_airports],
            "route_probe_distance_nm": route_probe.distance_nm,
        },
        "performance": {
            "validation_status": manifest.validation_status,
            "source_document": manifest.source_document,
            "source_revision": manifest.source_revision,
            "climb_temperature_policy": (manifest.climb_temperature_policy.value),
            "climb_row_count": len(performance.climb_rows),
            "cruise_row_count": len(performance.cruise_rows),
            "nominal_climb": {
                "altitude_ft": nominal_altitude_ft,
                "temperature_c": nominal_temperature_c,
                "weight_lb": weights[0],
                "time_min": nominal_climb.time_min,
                "fuel_gal": nominal_climb.fuel_gal,
                "distance_nm": nominal_climb.distance_nm,
            },
            "nominal_cruise": nominal_cruise.row.model_dump(),
        },
    }
