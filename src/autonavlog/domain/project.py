from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .enums import (
    FlightPhase,
    ProjectStatus,
    RouteNodeNameSource,
    RouteNodeRole,
    VisualReferenceRole,
)
from .weather import ForecastModel, legacy_forecast_model

JST = ZoneInfo("Asia/Tokyo")


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Airport(DomainModel):
    id: str
    icao: str
    name: str
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    elevation_ft_msl: float
    pattern_altitude_ft_msl: float | None = None
    source: str
    source_revision: str


class RouteNode(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    name: str = Field(min_length=1)
    name_source: RouteNodeNameSource = RouteNodeNameSource.IMPORTED
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    role: RouteNodeRole
    source: str = "MANUAL"
    manual_true_course_deg: float | None = Field(default=None, ge=0, lt=360)
    manual_distance_nm: float | None = Field(default=None, gt=0)


class VisualReference(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    role: VisualReferenceRole
    linked_section_id: UUID | None = None
    source: str = "MANUAL"


class ManualWind(DomainModel):
    direction_deg_from: int = Field(ge=1, le=360)
    speed_kt: float = Field(ge=0, le=200)

    @field_validator("direction_deg_from")
    @classmethod
    def normalize_north(cls, value: int) -> int:
        return value % 360

    @field_serializer("direction_deg_from")
    def serialize_north(self, value: int) -> int:
        return 360 if value == 0 else value


class FtdWeatherSettings(DomainModel):
    """Deterministic weather inputs used for flight-training-device runs."""

    surface_wind: ManualWind
    wind_at_5000_ft: ManualWind


class NavSection(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    sequence: int = Field(ge=0)
    from_node_id: UUID
    to_node_id: UUID
    phase: FlightPhase
    planned_altitude_ft_msl: float
    manual_wind_direction_deg: int | None = Field(default=None, ge=1, le=360)
    manual_wind_speed_kt: float | None = Field(default=None, ge=0)
    manual_wind_by_phase: dict[FlightPhase, ManualWind] = Field(default_factory=dict)
    manual_temperature_c: float | None = None
    manual_temperature_c_by_phase: dict[
        FlightPhase,
        Annotated[float, Field(ge=-80, le=60)],
    ] = Field(default_factory=dict)
    # ``manual_tas_kt`` is retained only while old Project JSON is being read.
    # New writes use the effective calculation phase as the key; a Physical
    # section can be split at RCA/EOC and must not share an override between
    # those resulting zones.
    manual_tas_kt_by_phase: dict[FlightPhase, Annotated[float, Field(gt=0)]] = Field(
        default_factory=dict
    )
    manual_tas_kt: float | None = Field(default=None, gt=0)

    def manual_tas_for_phase(self, phase: FlightPhase) -> float | None:
        """Return an override for one effective calculation phase.

        The scalar fallback deliberately applies only to the physical
        section's own phase.  It keeps in-memory legacy callers safe without
        recreating the historical cross-phase leak.
        """
        return self.manual_tas_kt_by_phase.get(
            phase,
            self.manual_tas_kt if phase == self.phase else None,
        )

    @model_validator(mode="after")
    def validate_manual_wind_pair(self) -> NavSection:
        values = (self.manual_wind_direction_deg, self.manual_wind_speed_kt)
        if (values[0] is None) != (values[1] is None):
            raise ValueError("manual wind direction and speed must be supplied together")
        return self

    @field_validator("manual_wind_direction_deg")
    @classmethod
    def normalize_manual_north(cls, value: int | None) -> int | None:
        return None if value is None else value % 360

    @field_serializer("manual_wind_direction_deg")
    def serialize_manual_north(self, value: int | None) -> int | None:
        return 360 if value == 0 else value


class Project(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    schema_version: Literal[5] = 5
    name: str = Field(min_length=1)
    pilot_name: str = ""
    ship_identifier: str = ""
    flight_date: date
    planned_departure_time_jst: datetime
    departure_airport_id: str
    destination_airport_id: str
    aircraft_profile_id: str = "SR22_G6"
    total_usable_fuel_gal: float = Field(gt=0)
    default_variation_deg_east: float
    weather_mode: Literal["FORECAST", "FTD"] = "FORECAST"
    ftd_weather: FtdWeatherSettings | None = None
    run_up_included: bool = True
    nose_fairing_enabled: bool = False
    air_conditioning_enabled: bool = True
    descent_rate_fpm: Literal[500, 1000] = 500
    tgl_count: int = Field(default=0, ge=0)
    selected_forecast_run_id: str | None = None
    selected_forecast_model: ForecastModel | None = None
    revision: int = Field(default=0, ge=0)
    status: ProjectStatus = ProjectStatus.DRAFT
    acknowledged_warning_codes: set[str] = Field(default_factory=set)
    route_nodes: list[RouteNode] = Field(default_factory=list)
    visual_references: list[VisualReference] = Field(default_factory=list)
    sections: list[NavSection] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version", mode="before")
    @classmethod
    def migrate_schema_version(cls, value: Any) -> Any:
        return 5 if value == 4 else value

    @model_validator(mode="after")
    def migrate_forecast_selection(self) -> Project:
        if "selected_forecast_model" not in self.model_fields_set:
            object.__setattr__(self, "selected_forecast_model",
                               legacy_forecast_model(self.selected_forecast_run_id))
        return self

    @field_validator("planned_departure_time_jst")
    @classmethod
    def normalize_departure_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("planned departure time must be timezone-aware")
        return value.astimezone(JST)

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_route_graph(self) -> Project:
        if self.weather_mode == "FTD" and self.ftd_weather is None:
            raise ValueError("FTD weather settings are required in FTD mode")
        node_ids = {node.id for node in self.route_nodes}
        if len(node_ids) != len(self.route_nodes):
            raise ValueError("route node ids must be unique")
        section_ids = {section.id for section in self.sections}
        if len(section_ids) != len(self.sections):
            raise ValueError("section ids must be unique")
        for section in self.sections:
            if section.from_node_id not in node_ids or section.to_node_id not in node_ids:
                raise ValueError("section endpoints must reference route nodes")
        return self

    def ordered_nodes(self) -> list[RouteNode]:
        return sorted(self.route_nodes, key=lambda item: item.sequence)

    def ordered_sections(self) -> list[NavSection]:
        return sorted(self.sections, key=lambda item: item.sequence)
