from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PerformanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PerformanceTableManifest(PerformanceModel):
    id: str
    file: str
    source_page: str
    sha256: str | None = None


class ClimbTemperaturePolicy(str, Enum):
    TABLE_GRID = "TABLE_GRID"
    ISA_BASELINE_10_PERCENT_PER_10C_ABOVE = "ISA_BASELINE_10_PERCENT_PER_10C_ABOVE"


class PerformanceManifest(PerformanceModel):
    schema_version: int = 1
    aircraft: str
    source_document: str
    source_revision: str
    verified_against: str
    validation_status: str = "UNVERIFIED"
    climb_temperature_policy: ClimbTemperaturePolicy = ClimbTemperaturePolicy.TABLE_GRID
    tables: list[PerformanceTableManifest] = Field(default_factory=list)

    @property
    def is_verified(self) -> bool:
        return self.validation_status == "VERIFIED"


class ClimbRow(PerformanceModel):
    pressure_altitude_ft: float
    temperature_c: float
    weight_lb: float
    cumulative_time_min: float = Field(ge=0)
    cumulative_fuel_gal: float = Field(ge=0)
    cumulative_distance_nm: float = Field(ge=0)
    source_page: str


class CruiseRow(PerformanceModel):
    pressure_altitude_ft: float
    isa_deviation_c: float
    rpm: float
    map_in_hg: float
    power_percent: float = Field(gt=0)
    ktas: float = Field(gt=0)
    gph: float = Field(gt=0)
    source_page: str
