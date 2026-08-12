from __future__ import annotations

import csv
import hashlib
from math import isfinite
from pathlib import Path, PurePosixPath
from typing import TypeVar

from pydantic import BaseModel

from .schemas import (
    ClimbRow,
    ClimbTemperaturePolicy,
    CruiseRow,
    PerformanceManifest,
)

RowT = TypeVar("RowT", bound=BaseModel)


class PerformanceDataError(ValueError):
    pass


class PerformanceRepository:
    def __init__(
        self,
        manifest: PerformanceManifest,
        climb_rows: list[ClimbRow],
        cruise_rows: list[CruiseRow],
        *,
        content_fingerprint: str | None = None,
        load_failure_reason: str | None = None,
        load_failure_detail: str | None = None,
    ):
        if load_failure_reason not in {None, "HASH_MISMATCH", "LOAD_FAILED"}:
            raise ValueError("unsupported performance load failure reason")
        if (load_failure_reason is None) != (load_failure_detail is None):
            raise ValueError("performance load failure reason and detail must be paired")
        self.manifest = manifest
        self.climb_rows = climb_rows
        self.cruise_rows = cruise_rows
        self.content_fingerprint = content_fingerprint
        self.load_failure_reason = load_failure_reason
        self.load_failure_detail = load_failure_detail
        self._validate_rows()

    @classmethod
    def from_directory(cls, directory: str | Path) -> PerformanceRepository:
        root = Path(directory)
        manifest = PerformanceManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        required = {"climb_time_fuel_distance", "cruise_performance"}
        table_ids = [table.id for table in manifest.tables]
        if len(table_ids) != len(set(table_ids)):
            raise PerformanceDataError("manifest contains duplicate performance table IDs")
        files = {table.id: table for table in manifest.tables}
        if set(files) != required:
            raise PerformanceDataError(
                "manifest must declare exactly the required performance tables"
            )
        paths: dict[str, Path] = {}
        contents: dict[str, bytes] = {}
        for table_id, table in files.items():
            path = cls._safe_table_path(root, table.file, table_id)
            if (
                table.sha256 is None
                or len(table.sha256) != 64
                or any(character not in "0123456789abcdefABCDEF" for character in table.sha256)
            ):
                raise PerformanceDataError(f"{table_id} must declare a 64-character SHA-256")
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != table.sha256.lower():
                raise PerformanceDataError(f"SHA-256 mismatch for {table.file}")
            paths[table_id] = path
            contents[table_id] = content
        climb = cls._read_csv(paths["climb_time_fuel_distance"], ClimbRow)
        cruise = cls._read_csv(paths["cruise_performance"], CruiseRow)
        content_digest = hashlib.sha256()
        for table_id in sorted(contents):
            for part in (table_id.encode("utf-8"), contents[table_id]):
                content_digest.update(len(part).to_bytes(8, "big"))
                content_digest.update(part)
        return cls(
            manifest,
            climb,
            cruise,
            content_fingerprint=content_digest.hexdigest(),
        )

    @classmethod
    def from_directory_for_application(
        cls,
        directory: str | Path,
    ) -> PerformanceRepository:
        """Load runtime data while preserving a fail-closed UI on data errors."""

        root = Path(directory)
        try:
            return cls.from_directory(root)
        except (OSError, UnicodeError, csv.Error, ValueError) as error:
            detail = str(error) or error.__class__.__name__
            reason = (
                "HASH_MISMATCH"
                if isinstance(error, PerformanceDataError) and detail.startswith("SHA-256 mismatch")
                else "LOAD_FAILED"
            )
            try:
                manifest = PerformanceManifest.model_validate_json(
                    (root / "manifest.json").read_text(encoding="utf-8")
                ).model_copy(update={"tables": []})
            except (OSError, UnicodeError, ValueError):
                manifest = PerformanceManifest(
                    aircraft="UNAVAILABLE",
                    source_document="unavailable",
                    source_revision="unavailable",
                    verified_against="unavailable",
                    validation_status="REJECTED",
                )
            return cls(
                manifest,
                [],
                [],
                load_failure_reason=reason,
                load_failure_detail=detail,
            )

    @staticmethod
    def _safe_table_path(root: Path, relative: str, table_id: str) -> Path:
        if "\\" in relative or any(ord(character) < 32 for character in relative):
            raise PerformanceDataError(f"unsafe table path for {table_id}")
        normalized = PurePosixPath(relative)
        if (
            normalized.is_absolute()
            or len(normalized.parts) != 1
            or normalized.name in {"", ".", ".."}
            or ":" in normalized.name
        ):
            raise PerformanceDataError(f"unsafe table path for {table_id}")
        path = root / normalized.name
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as error:
            raise PerformanceDataError(f"table path escapes repository for {table_id}") from error
        if path.is_symlink():
            raise PerformanceDataError(f"performance table must not be a symbolic link: {table_id}")
        if not path.is_file():
            raise PerformanceDataError(f"performance table is absent: {table_id}")
        return path

    @staticmethod
    def _read_csv(path: Path, model: type[RowT]) -> list[RowT]:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames
            if fields is None or len(fields) != len(set(fields)):
                raise PerformanceDataError(f"CSV has missing or duplicate headers: {path.name}")
            return [model.model_validate(row) for row in reader]

    def validation_issue_reason(self) -> str | None:
        if self.load_failure_reason == "HASH_MISMATCH":
            return "HASH_MISMATCH"
        if self.load_failure_reason is not None:
            return None
        status = self.manifest.validation_status
        if status == "VERIFIED":
            return None
        if status in {"UNVERIFIED", "PENDING", "REJECTED"}:
            return status
        return "UNKNOWN_STATUS"

    def _validate_rows(self) -> None:
        for label, rows in (("climb", self.climb_rows), ("cruise", self.cruise_rows)):
            if any(not row.source_page for row in rows):
                raise PerformanceDataError(f"{label} table has missing source pages")
            for row in rows:
                numeric = [
                    value for value in row.model_dump().values() if isinstance(value, (int, float))
                ]
                if not all(isfinite(value) for value in numeric):
                    raise PerformanceDataError(f"{label} table contains a non-finite value")
        climb_keys = [
            (row.weight_lb, row.pressure_altitude_ft, row.temperature_c) for row in self.climb_rows
        ]
        if len(climb_keys) != len(set(climb_keys)):
            raise PerformanceDataError("climb table contains duplicate axis rows")
        cruise_keys = [
            (
                row.pressure_altitude_ft,
                row.isa_deviation_c,
                row.power_percent,
            )
            for row in self.cruise_rows
        ]
        if len(cruise_keys) != len(set(cruise_keys)):
            raise PerformanceDataError(
                "cruise table contains duplicate altitude/ISA/power rows"
            )
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
        if (
            self.manifest.climb_temperature_policy
            == ClimbTemperaturePolicy.ISA_BASELINE_10_PERCENT_PER_10C_ABOVE
        ):
            self._validate_isa_baseline_climb_rows()
            return
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

    def _validate_isa_baseline_climb_rows(self) -> None:
        groups: dict[float, list[ClimbRow]] = {}
        for row in self.climb_rows:
            groups.setdefault(row.weight_lb, []).append(row)
        for weight, rows in groups.items():
            altitudes = [row.pressure_altitude_ft for row in rows]
            if len(altitudes) != len(set(altitudes)):
                raise PerformanceDataError(
                    "ISA baseline climb table must contain exactly one row "
                    f"per altitude at weight {weight:g}"
                )
            ordered = sorted(
                rows,
                key=lambda item: item.pressure_altitude_ft,
            )
            for previous, current in zip(
                ordered,
                ordered[1:],
                strict=False,
            ):
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
                        f"ISA baseline climb cumulative values decrease at weight {weight:g}"
                    )
                if current.temperature_c > previous.temperature_c:
                    raise PerformanceDataError(
                        "ISA baseline climb OAT must not increase with altitude "
                        f"at weight {weight:g}"
                    )

    @staticmethod
    def _canonical_profile_id(value: str) -> str:
        parts = "".join(
            character if character.isalnum() else " " for character in value.upper()
        ).split()
        return "_".join(parts)

    def readiness_problems(
        self,
        aircraft_profile_id: str = "SR22_G6",
    ) -> tuple[str, ...]:
        """Return deterministic reasons this table cannot support NAV2 planning."""

        if self.load_failure_detail is not None:
            return (
                "性能データを安全に読み込めません"
                f"（{self.load_failure_reason}）: {self.load_failure_detail}",
            )

        problems: list[str] = []
        manifest_profile = self._canonical_profile_id(self.manifest.aircraft)
        requested_profile = self._canonical_profile_id(aircraft_profile_id)
        if not requested_profile or manifest_profile != requested_profile:
            problems.append(
                "機体Profile "
                f"{aircraft_profile_id or '未設定'}を性能manifest "
                f"{self.manifest.aircraft!r}で解決できません"
            )
        if not self.climb_rows:
            problems.append("climb性能表に行がありません")
        if not self.cruise_rows:
            problems.append("cruise性能表に行がありません")
        if problems:
            return tuple(problems)

        from .climb import ClimbCalculator, ClimbPerformanceError
        from .cruise import (
            CruisePerformanceError,
            CruisePerformanceSelectionPolicy,
        )

        weights = sorted({row.weight_lb for row in self.climb_rows})
        try:
            ClimbCalculator(
                self.climb_rows,
                self.manifest.climb_temperature_policy,
            ).calculate(
                departure_pressure_altitude_ft=0.0,
                cruise_pressure_altitude_ft=5_000.0,
                temperature_c=15.0,
                weight_lb=weights[0],
            )
        except ClimbPerformanceError as error:
            problems.append(f"標準climb条件（SL→5,000 ft / 15 ℃）を解決できません: {error}")
        try:
            CruisePerformanceSelectionPolicy(self.cruise_rows).select(
                pressure_altitude_ft=5_000.0,
                isa_deviation_c=0.0,
                distance_nm=100.0,
                true_course_deg=0.0,
                wind_direction_deg_from=None,
                wind_speed_kt=0.0,
            )
        except CruisePerformanceError as error:
            problems.append(f"標準cruise条件（5,000 ft / ISA）を解決できません: {error}")
        return tuple(problems)

    def require_verified(self) -> None:
        if self.load_failure_detail is not None:
            raise PerformanceDataError(f"performance data load failed: {self.load_failure_detail}")
        if not self.manifest.is_verified:
            raise PerformanceDataError("performance data has not completed source verification")
        if not self.climb_rows or not self.cruise_rows:
            raise PerformanceDataError("verified performance tables cannot be empty")
