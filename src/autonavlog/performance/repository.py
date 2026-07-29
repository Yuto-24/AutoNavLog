from __future__ import annotations

import csv
import hashlib
from math import isfinite
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from .schemas import ClimbRow, CruiseRow, PerformanceManifest

RowT = TypeVar("RowT", bound=BaseModel)


class PerformanceDataError(ValueError):
    pass


class PerformanceRepository:
    def __init__(
        self,
        manifest: PerformanceManifest,
        climb_rows: list[ClimbRow],
        cruise_rows: list[CruiseRow],
    ):
        self.manifest = manifest
        self.climb_rows = climb_rows
        self.cruise_rows = cruise_rows
        self._validate_rows()

    @classmethod
    def from_directory(cls, directory: str | Path) -> PerformanceRepository:
        root = Path(directory)
        manifest = PerformanceManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        files = {table.id: table for table in manifest.tables}
        required = {"climb_time_fuel_distance", "cruise_performance"}
        if not required.issubset(files):
            raise PerformanceDataError("manifest does not declare all required performance tables")
        for table in files.values():
            path = root / table.file
            if table.sha256 is not None:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != table.sha256:
                    raise PerformanceDataError(f"SHA-256 mismatch for {table.file}")
        climb = cls._read_csv(root / files["climb_time_fuel_distance"].file, ClimbRow)
        cruise = cls._read_csv(root / files["cruise_performance"].file, CruiseRow)
        return cls(manifest, climb, cruise)

    @staticmethod
    def _read_csv(path: Path, model: type[RowT]) -> list[RowT]:
        with path.open(newline="", encoding="utf-8") as handle:
            return [model.model_validate(row) for row in csv.DictReader(handle)]

    def _validate_rows(self) -> None:
        for label, rows in (("climb", self.climb_rows), ("cruise", self.cruise_rows)):
            if any(not row.source_page for row in rows):
                raise PerformanceDataError(f"{label} table has missing source pages")
            for row in rows:
                numeric = [
                    value
                    for value in row.model_dump().values()
                    if isinstance(value, (int, float))
                ]
                if not all(isfinite(value) for value in numeric):
                    raise PerformanceDataError(f"{label} table contains a non-finite value")
        climb_keys = [
            (row.weight_lb, row.pressure_altitude_ft, row.temperature_c)
            for row in self.climb_rows
        ]
        if len(climb_keys) != len(set(climb_keys)):
            raise PerformanceDataError("climb table contains duplicate axis rows")
        cruise_keys = [
            (
                row.pressure_altitude_ft,
                row.isa_deviation_c,
                row.rpm,
                row.map_in_hg,
                row.power_percent,
            )
            for row in self.cruise_rows
        ]
        if len(cruise_keys) != len(set(cruise_keys)):
            raise PerformanceDataError("cruise table contains duplicate axis rows")
        self._validate_climb_monotonicity()
        if self.manifest.is_verified:
            pending_pages = [
                table.id
                for table in self.manifest.tables
                if not table.source_page or table.source_page == "PENDING"
            ]
            if pending_pages:
                raise PerformanceDataError(
                    f"verified manifest has pending source pages: {pending_pages}"
                )

    def _validate_climb_monotonicity(self) -> None:
        groups: dict[tuple[float, float], list[ClimbRow]] = {}
        for row in self.climb_rows:
            groups.setdefault((row.weight_lb, row.temperature_c), []).append(row)
        for key, rows in groups.items():
            ordered = sorted(rows, key=lambda item: item.pressure_altitude_ft)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                previous_values = (
                    previous.cumulative_time_min,
                    previous.cumulative_fuel_gal,
                    previous.cumulative_distance_nm,
                )
                current_values = (
                    current.cumulative_time_min,
                    current.cumulative_fuel_gal,
                    current.cumulative_distance_nm,
                )
                if any(
                    after < before
                    for before, after in zip(
                        previous_values,
                        current_values,
                        strict=True,
                    )
                ):
                    raise PerformanceDataError(
                        f"climb cumulative values decrease for weight/temperature {key}"
                    )

    def require_verified(self) -> None:
        if not self.manifest.is_verified:
            raise PerformanceDataError("performance data has not completed source verification")
        if not self.climb_rows or not self.cruise_rows:
            raise PerformanceDataError("verified performance tables cannot be empty")
