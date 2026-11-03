"""Versioned compute-only boundary shared by Local and the retained forecast server."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autonavlog.application.project_fingerprints import forecast_selection_fingerprint
from autonavlog.domain.project import Project
from autonavlog.web.models import WorkingCalculation

# Explicit capabilities supplement the exact Python/data identity. An older engine
# cannot silently ignore current VOR, rounding, phase or reference semantics.
FEATURES = ("fixed-run-refresh", "phase-weather", "rounding", "vor-selection")
WEATHER_FAILURES = frozenset(
    {
        "WEATHER_CATALOG_EXPIRED",
        "WEATHER_COMMUNICATION_FAILED",
        "WEATHER_DISCOVERY_FAILED",
        "WEATHER_SOURCE_UNAVAILABLE",
        "WEATHER_TRANSPORT_FAILED",
        "WEATHER_PREPARED_UNAVAILABLE",
        "WEATHER_RUN_UNAVAILABLE",
        "WEATHER_OUT_OF_COVERAGE",
        "FORECAST_RUN_OUT_OF_COVERAGE",
    }
)


class BackupModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackupContract(BackupModel):
    schema_version: Literal[1] = 1
    engine_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    data_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    features: tuple[str, ...] = FEATURES


def _tree_hash(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


@lru_cache(maxsize=4)
def calculation_contract(data_root: Path) -> BackupContract:
    package = Path(__file__).parent
    files = [
        p
        for folder in ("performance", "reference")
        for p in (data_root / folder).rglob("*")
        if p.is_file()
    ]
    return BackupContract(
        engine_sha256=_tree_hash(package, list(package.rglob("*.py"))),
        data_sha256=_tree_hash(data_root, files),
    )


def calculation_project(project: Project) -> Project:
    """Transfer only this calculation; no owner, recovery, history or import data."""
    result = project.model_copy(deep=True)
    result.name = "route"
    result.pilot_name = ""
    result.ship_identifier = ""
    result.metadata = {"ui_state": result.metadata.get("ui_state", {})}
    return result


def input_identity(project: Project, rounding_mode: bool, refresh_forecast: bool) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "calculation": forecast_selection_fingerprint(project, rounding_mode=rounding_mode),
                "revision": project.revision,
                "selected_run": project.selected_forecast_run_id,
                "refresh_forecast": refresh_forecast,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


class BackupInput(BackupModel):
    contract: BackupContract
    project: Project
    rounding_mode: bool = Field(strict=True)
    refresh_forecast: bool = Field(strict=True)
    input_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str

    @field_validator("project")
    @classmethod
    def bounded_project(cls, value: Project) -> Project:
        # Resource budgets, not meteorological coverage rules.
        if (
            len(value.route_nodes) > 64
            or len(value.sections) > 64
            or len(value.visual_references) > 128
            or value.tgl_count > 20
            or len(canonical_json(value.model_dump(mode="json")).encode()) > 240 * 1024
        ):
            raise ValueError("calculation input exceeds resource budget")
        if value != calculation_project(value) or value.weather_mode != "FORECAST":
            raise ValueError("calculation-only project required")
        return value

    @field_validator("reason")
    @classmethod
    def known_reason(cls, value: str) -> str:
        if value not in WEATHER_FAILURES:
            raise ValueError("unsupported weather failure")
        return value


class BackupRequest(BackupInput):
    request_id: UUID
    issued_at: datetime

    @field_validator("issued_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return value


class BackupResult(BackupModel):
    contract: BackupContract
    request_id: UUID
    input_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    rounding_mode: bool = Field(strict=True)
    refresh_available: bool = Field(strict=True)
    calculation: WorkingCalculation
    calculation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_checksum(self) -> BackupResult:
        actual = hashlib.sha256(
            canonical_json(self.calculation.model_dump(mode="json")).encode()
        ).hexdigest()
        if actual != self.calculation_sha256:
            raise ValueError("calculation checksum mismatch")
        return self


def canonical_json(value: Any) -> str:
    # Untyped UI metadata crosses JSON and must not distinguish 1 from 1.0.
    def normalize(item: Any) -> Any:
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, dict):
            return {key: normalize(val) for key, val in item.items()}
        if isinstance(item, (tuple, list)):
            return [normalize(val) for val in item]
        return item

    return json.dumps(
        normalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
