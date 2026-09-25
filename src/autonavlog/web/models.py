from __future__ import annotations

import json
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autonavlog.domain.calculation import CalculationOutcome
from autonavlog.domain.enums import FlightPhase
from autonavlog.domain.planning import ArrivalAltitudeMode
from autonavlog.domain.project import FtdWeatherSettings, ManualWind, Project
from autonavlog.importers.kml import KmlImportResult
from autonavlog.weather.destination_taf import DestinationWindForecast


class WebRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ImportRouteRequest(WebRequestModel):
    filename: str = Field(default="pasted.kml", min_length=1, max_length=255)
    content_base64: str | None = None
    kml_text: str | None = Field(
        default=None,
        max_length=10 * 1024 * 1024,
    )
    kmz_kml_filename: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> ImportRouteRequest:
        supplied = int(self.content_base64 is not None) + int(self.kml_text is not None)
        if supplied != 1:
            raise ValueError("content_base64 or kml_text must be supplied, but not both")
        return self


class ConfirmRouteRequest(WebRequestModel):
    candidate_kind: Literal["line", "connected_lines", "polygon", "points"]
    candidate_index: int = Field(default=0, ge=0)
    point_indices: list[Annotated[int, Field(ge=0)]] = Field(
        default_factory=list,
        max_length=500,
    )
    route_use_confirmed: bool
    polygon_route_confirmed: bool = False
    flight_date: date
    departure_time_jst: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    pilot_name: str = Field(default="", max_length=100)
    ship_identifier: str = Field(default="", max_length=100)
    total_usable_fuel_gal: float = Field(default=90.0, gt=0, le=200)
    default_variation_deg_east: float = Field(default=8.0, ge=-30, le=30)
    weather_mode: Literal["FORECAST", "FTD"] = "FORECAST"
    ftd_weather: FtdWeatherSettings | None = None
    run_up_included: bool = True
    nose_fairing_enabled: bool = False
    air_conditioning_enabled: bool = True
    descent_rate_fpm: Literal[500, 1000] = 500
    tgl_count: int = Field(default=0, ge=0, le=20)
    all_leg_altitude_ft_msl: float = Field(default=3000, gt=0, le=25_000)
    use_penultimate_as_vrep: bool = True
    defaults_confirmed: bool = False

    @model_validator(mode="after")
    def validate_ftd_weather(self) -> ConfirmRouteRequest:
        if self.weather_mode == "FTD" and self.ftd_weather is None:
            raise ValueError("ftd_weather is required in FTD mode")
        return self


class SectionUpdate(WebRequestModel):
    section_id: UUID
    planned_altitude_ft_msl: float = Field(gt=0, le=25_000)
    phase: FlightPhase
    manual_wind_direction_deg: int | None = Field(default=None, ge=1, le=360)
    manual_wind_speed_kt: float | None = Field(default=None, ge=0, le=200)
    manual_wind_by_phase: dict[FlightPhase, ManualWind] | None = None
    manual_temperature_c: float | None = Field(default=None, ge=-80, le=60)
    manual_temperature_c_by_phase: (
        dict[
            FlightPhase,
            Annotated[float, Field(ge=-80, le=60)],
        ]
        | None
    ) = None
    manual_tas_kt_by_phase: dict[
        FlightPhase,
        Annotated[float, Field(gt=0, le=300)],
    ] | None = None
    manual_tas_kt: float | None = Field(default=None, gt=0, le=300)

    @field_validator("manual_wind_direction_deg")
    @classmethod
    def normalize_north(cls, value: int | None) -> int | None:
        return None if value is None else value % 360

    @model_validator(mode="after")
    def validate_wind_pair(self) -> SectionUpdate:
        if (self.manual_wind_direction_deg is None) != (self.manual_wind_speed_kt is None):
            raise ValueError("manual wind direction and speed must be supplied together")
        return self


class UpdateProjectRequest(WebRequestModel):
    flight_date: date
    departure_time_jst: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    pilot_name: str | None = Field(default=None, max_length=100)
    ship_identifier: str | None = Field(default=None, max_length=100)
    total_usable_fuel_gal: float = Field(gt=0, le=200)
    default_variation_deg_east: float = Field(ge=-30, le=30)
    weather_mode: Literal["FORECAST", "FTD"] = "FORECAST"
    ftd_weather: FtdWeatherSettings | None = None
    run_up_included: bool = True
    nose_fairing_enabled: bool = False
    air_conditioning_enabled: bool = True
    descent_rate_fpm: Literal[500, 1000] = 500
    tgl_count: int = Field(default=0, ge=0, le=20)
    sections: list[SectionUpdate] = Field(default_factory=list, max_length=500)
    visual_reporting_point_node_id: UUID | None = None
    selected_pattern_altitude_ft_msl: int | None = Field(
        default=None,
        ge=100,
        le=25_000,
        multiple_of=100,
    )
    arrival_altitude_mode: ArrivalAltitudeMode = ArrivalAltitudeMode.STANDARD_DISTANCE_RULE
    manual_vrep_altitude_ft_msl: int | None = Field(
        default=None,
        ge=-1000,
        le=25_000,
        multiple_of=100,
    )
    manual_vrep_reason: str | None = Field(default=None, max_length=500)
    defaults_confirmed: bool = False

    @model_validator(mode="after")
    def validate_ftd_weather(self) -> UpdateProjectRequest:
        if self.weather_mode == "FTD" and self.ftd_weather is None:
            raise ValueError("ftd_weather is required in FTD mode")
        return self


class CheckPointInput(WebRequestModel):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    linked_section_id: UUID


class ReplaceCheckPointsRequest(WebRequestModel):
    check_points: list[CheckPointInput] = Field(default_factory=list, max_length=500)


class RenameRouteNodeRequest(WebRequestModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("route node name must not be blank")
        return value.strip()


class AcknowledgeRequest(WebRequestModel):
    checked: bool


class SaveProjectRequest(WebRequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)


class LoadProjectRequest(WebRequestModel):
    project_id: UUID


class WorkingCalculation(BaseModel):
    """Calculation-time inputs retained with the last-good result for explicit save."""

    model_config = ConfigDict(extra="forbid")
    project: Project
    outcome: CalculationOutcome
    destination_wind: DestinationWindForecast | None
    forecast_metadata: dict[str, Any] = Field(default_factory=dict)
    calculation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("outcome", mode="before")
    @classmethod
    def decode_outcome(cls, value: object) -> object:
        if isinstance(value, dict):
            return CalculationOutcome.model_validate_json(json.dumps(value))
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> WorkingCalculation:
        if self.project.id != self.outcome.project_id:
            raise ValueError("calculation must belong to its Project")
        ui_state = self.project.metadata.get("ui_state")
        if not isinstance(ui_state, dict) or (
            ui_state.get("calculated_against_fingerprint") != self.calculation_fingerprint
        ):
            raise ValueError("calculation fingerprint must match its Project")
        if self.project.selected_forecast_run_id != self.outcome.selected_forecast_run_id:
            raise ValueError("calculation forecast run must match its Project")
        if self.project.selected_forecast_model != self.outcome.selected_forecast_model:
            raise ValueError("calculation forecast model must match its Project")
        return self


class WorkingRecovery(BaseModel):
    """Transient working copy, never a repository record or runtime handle."""

    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    project: Project | None
    outcome: CalculationOutcome | None
    destination_wind: DestinationWindForecast | None
    import_result: KmlImportResult | None
    import_filename: str | None
    last_calculation: WorkingCalculation | None = None

    @field_validator("outcome", mode="before")
    @classmethod
    def decode_outcome(cls, value: object) -> object:
        # Strict nested domain dataclasses need JSON-mode UUID decoding.
        if isinstance(value, dict):
            return CalculationOutcome.model_validate_json(json.dumps(value))
        return value

    @model_validator(mode="after")
    def validate_project_identity(self) -> WorkingRecovery:
        if self.outcome is not None and (
            self.project is None or self.outcome.project_id != self.project.id
        ):
            raise ValueError("last-good calculation must belong to the working Project")
        if self.last_calculation is not None and (
            self.project is None or self.last_calculation.project.id != self.project.id
        ):
            raise ValueError("last calculation must belong to the working Project")
        return self
