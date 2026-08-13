from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    FlightPhase,
    ProjectStatus,
    RouteNodeRole,
    VisualReferenceRole,
)

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
    project_id: UUID | None = None
    sequence: int = Field(ge=0)
    name: str = Field(min_length=1)
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    role: RouteNodeRole
    source: str = "MANUAL"
    manual_true_course_deg: float | None = Field(default=None, ge=0, lt=360)
    manual_distance_nm: float | None = Field(default=None, gt=0)


class VisualReference(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID | None = None
    name: str = Field(min_length=1)
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    role: VisualReferenceRole
    linked_section_id: UUID | None = None
    along_track_fraction: float | None = Field(default=None, ge=0, le=1)
    source: str = "MANUAL"


class ManualWind(DomainModel):
    direction_deg_from: float = Field(ge=0, lt=360)
    speed_kt: float = Field(ge=0, le=200)


class NavSection(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID | None = None
    sequence: int = Field(ge=0)
    from_node_id: UUID
    to_node_id: UUID
    phase: FlightPhase
    planned_altitude_ft_msl: float
    safe_enroute_altitude_ft_msl: float | None = None
    manual_wind_direction_deg: float | None = Field(default=None, ge=0, lt=360)
    manual_wind_speed_kt: float | None = Field(default=None, ge=0)
    manual_wind_by_phase: dict[FlightPhase, ManualWind] = Field(default_factory=dict)
    manual_temperature_c: float | None = None
    manual_temperature_c_by_phase: dict[
        FlightPhase,
        Annotated[float, Field(ge=-80, le=60)],
    ] = Field(default_factory=dict)
    manual_tas_kt: float | None = Field(default=None, gt=0)
    loss_time_seconds: float = Field(default=0, ge=0)
    notes: str = ""

    @model_validator(mode="after")
    def validate_manual_wind_pair(self) -> NavSection:
        values = (self.manual_wind_direction_deg, self.manual_wind_speed_kt)
        if (values[0] is None) != (values[1] is None):
            raise ValueError("manual wind direction and speed must be supplied together")
        return self


class Project(DomainModel):
    id: UUID = Field(default_factory=uuid4)
    schema_version: Literal[1] = 1
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
    manual_qnh_hpa: float | None = Field(default=None, gt=800, lt=1100)
    tgl_count: int = Field(default=0, ge=0)
    selected_forecast_run_id: str | None = None
    revision: int = Field(default=0, ge=0)
    status: ProjectStatus = ProjectStatus.DRAFT
    acknowledged_warning_codes: set[str] = Field(default_factory=set)
    route_nodes: list[RouteNode] = Field(default_factory=list)
    visual_references: list[VisualReference] = Field(default_factory=list)
    sections: list[NavSection] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)

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
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_route_graph(self) -> Project:
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
