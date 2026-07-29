from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .calculation import CalculationOutcome, Issue
from .project import Project
from .weather import WeatherRequest, WeatherResult


class CalculationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    project_revision: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    input_data: Project
    adopted_manual_overrides: dict[str, Any] = Field(default_factory=dict)
    performance_table_version: str | None
    calculation_policy_version: str
    calculation_results: CalculationOutcome
    selected_forecast_run_id: str | None
    forecast_metadata: dict[str, Any] = Field(default_factory=dict)
    weather_requests: list[WeatherRequest] = Field(default_factory=list)
    weather_results: list[WeatherResult] = Field(default_factory=list)
    autonavlog_version: str
    msm_package_version: str | None
    warnings: list[Issue] = Field(default_factory=list)
    readiness_status: str
