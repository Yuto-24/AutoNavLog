"""Portable MSM consumer. Transport and disposable storage belong to the browser.

The catalog is a static delivery index; weather records remain MsmPreparedData.
Only the upstream library selects Runs and interprets their meteorological coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jma_gpv_weather import (
    CoveragePoint,
    ForecastRequirements,
    MsmClient,
    MsmPreparedData,
    RunId,
)
from jma_gpv_weather.errors import (
    CacheIntegrityError,
    MissingVariableError,
    NoCompatibleRunError,
    SelectedRunCoverageError,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autonavlog.domain.project import Project
from autonavlog.domain.weather import ForecastRequirement
from autonavlog.weather.msm_adapter import MsmWeatherProvider

CATALOG_CLOCK_SKEW = timedelta(minutes=5)


class LocalWeatherError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class PreparedAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run: str = Field(pattern=r"^\d{14}$")
    file: str = Field(pattern=r"^[a-f0-9]{64}\.npz$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(gt=0, le=32 * 1024 * 1024)

    @model_validator(mode="after")
    def check_name(self) -> PreparedAsset:
        if self.file != f"{self.sha256}.npz":
            raise ValueError("prepared asset filename must identify its content hash")
        return self


class WeatherCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(strict=True, ge=1, le=1)
    generated_at: datetime
    expires_at: datetime
    listings: dict[str, str]
    assets: list[PreparedAsset]

    @field_validator("generated_at", "expires_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("catalog timestamps require timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def valid_catalog(self) -> WeatherCatalog:
        if self.expires_at <= self.generated_at:
            raise ValueError("catalog expiry must follow generation")
        if len({asset.run for asset in self.assets}) != len(self.assets):
            raise ValueError("duplicate Run assets")
        return self


class LocalMsmClient(MsmClient):  # type: ignore[misc]
    """Reuse the public client without desktop acquisition, filesystem or decoder."""

    def __init__(self) -> None:
        super().__init__()
        self.catalog: WeatherCatalog | None = None
        self.data: MsmPreparedData | None = None

    def discover_runs(self, requirements: ForecastRequirements, **kwargs: Any) -> Any:
        if self.catalog is None:
            raise LocalWeatherError("WEATHER_DISCOVERY_FAILED", "MSM配信情報がありません。")
        try:
            return super().discover_runs(requirements, listings=self.catalog.listings)
        except NoCompatibleRunError as error:
            # A successful listing with no files is not an offline model exclusion.
            raise LocalWeatherError(
                "WEATHER_RUN_UNAVAILABLE", "必要なMSM Runがsourceにありません。"
            ) from error
        except Exception as error:
            raise LocalWeatherError(
                "WEATHER_DISCOVERY_FAILED", "MSM Run一覧を確認できません。"
            ) from error

    def resolve_run(
        self,
        requirements: ForecastRequirements,
        selected_run: RunId | None = None,
        available_runs: Any = None,
    ) -> Any:
        coverage = self.check_coverage(requirements, run=selected_run)
        if coverage.outside_spec:
            raise LocalWeatherError("WEATHER_OUT_OF_COVERAGE", ", ".join(coverage.reason_codes))
        status = super().resolve_run(requirements, selected_run, available_runs)
        if not status.selected_run_covers_request:
            raise LocalWeatherError(
                "WEATHER_RUN_UNAVAILABLE", "保存済みMSM Runを取得できません。Runは変更しません。"
            )
        return status

    def prepare_run(
        self,
        run: RunId,
        requirements: ForecastRequirements,
        terrain_provider: Any = None,
        **kwargs: Any,
    ) -> Any:
        if self.data is None:
            raise LocalWeatherError("WEATHER_PREPARED_UNAVAILABLE", "MSM配信データがありません。")
        try:
            return super().prepare_run(
                run,
                requirements,
                available_runs=self.discover_runs(requirements),
                terrain_provider=terrain_provider,
                prepared_data=self.data,
            )
        except CacheIntegrityError as error:
            raise LocalWeatherError("WEATHER_PAYLOAD_INTEGRITY_FAILED", str(error)) from error
        except (MissingVariableError, SelectedRunCoverageError) as error:
            raise LocalWeatherError("WEATHER_PREPARED_UNAVAILABLE", str(error)) from error


class LocalMsmWeather:
    def __init__(self) -> None:
        self.client = LocalMsmClient()
        self.provider = MsmWeatherProvider("unused-browser-cache", client=self.client)
        self._planned: tuple[RunId, ForecastRequirements] | None = None

    def plan(
        self,
        project: Project,
        requirement: ForecastRequirement,
        catalog_json: str,
    ) -> str:
        # Never retain arrays from an earlier calculation as an acquisition fallback.
        self.client.data = None
        self.client.catalog = None
        self._planned = None
        self.provider._prepared.clear()
        try:
            catalog = WeatherCatalog.model_validate_json(catalog_json)
        except ValueError as error:
            raise LocalWeatherError(
                "WEATHER_CATALOG_INVALID", "MSM配信情報が破損しています。"
            ) from error
        now = datetime.now(UTC)
        # Producer and device clocks differ. Tolerate a slightly future generation
        # timestamp, but never extend the catalog's absolute expiry.
        if catalog.expires_at <= now or catalog.generated_at > now + CATALOG_CLOCK_SKEW:
            raise LocalWeatherError(
                "WEATHER_CATALOG_EXPIRED", "MSM配信情報の有効期限を確認してください。"
            )
        self.client.catalog = catalog
        native = self.provider._requirement(requirement)
        selected = (
            self.provider._run_id(project.selected_forecast_run_id)
            if project.selected_forecast_run_id
            else None
        )
        coverage = self.client.check_coverage(
            native,
            run=selected,
            points=tuple(
                CoveragePoint(node.latitude_deg, node.longitude_deg) for node in project.route_nodes
            ),
        )
        if coverage.outside_spec:
            raise LocalWeatherError("WEATHER_OUT_OF_COVERAGE", ", ".join(coverage.reason_codes))
        status = self.client.resolve_run(native, selected_run=selected)
        asset = next(
            (item for item in catalog.assets if item.run == str(status.selected_run)), None
        )
        if asset is None:
            raise LocalWeatherError(
                "WEATHER_PREPARED_UNAVAILABLE", "選択したMSM Runの配信データがありません。"
            )
        self._planned = (status.selected_run, native)
        return asset.model_dump_json()

    def accept(self, payload: bytes, sha256: str) -> None:
        self.client.data = None
        try:
            self.client.data = MsmPreparedData.from_bytes(payload, expected_sha256=sha256)
            if self._planned is None:
                raise LocalWeatherError("WEATHER_PROCESSING_FAILED", "MSM取得計画がありません。")
            self.client.prepare_run(*self._planned)
        except CacheIntegrityError as error:
            raise LocalWeatherError("WEATHER_PAYLOAD_INTEGRITY_FAILED", str(error)) from error
