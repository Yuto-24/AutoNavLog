"""Demand-driven browser transport for the upstream portable MSM/GSM clients.

A calculation may request a catalog or an exact Run payload and resume after the
Worker supplies it. Only the application policy selects models; the public library
owns discovery, payload validation, preparation and all meteorological decisions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from jma_gpv_weather import GsmClient, MsmClient

from autonavlog.domain.weather import ForecastModel
from autonavlog.weather.forecast_provider import ForecastWeatherProvider
from autonavlog.weather.gsm_adapter import GsmWeatherProvider
from autonavlog.weather.local_msm import (
    CATALOG_CLOCK_SKEW,
    LocalWeatherError,
    PreparedAsset,
    WeatherCatalog,
)
from autonavlog.weather.msm_adapter import MsmWeatherProvider


class LocalWeatherRequest(BaseException):
    """Suspend the synchronous calculation, without classifying it as a failure.

    Deliberately outside Exception: processing-error handlers must not convert a
    transport suspension into a completed calculation or consume refresh intent.
    Only LocalApplication's Worker boundary catches this signal.
    """

    def __init__(self, model: ForecastModel, asset: PreparedAsset | None = None) -> None:
        self.request = {
            "model": model,
            "kind": "catalog" if asset is None else "prepared",
            "asset": None if asset is None else asset.model_dump(mode="json"),
        }


class _PortableClient:
    model: ForecastModel

    def __init__(self) -> None:
        super().__init__()
        self.catalog: WeatherCatalog | None = None
        self.payloads: dict[str, Any] = {}

    def clear(self) -> None:
        self.catalog = None
        self.payloads.clear()

    def discover_runs(self, requirements: Any, **kwargs: Any) -> Any:
        if self.catalog is None:
            raise LocalWeatherRequest(self.model)
        # Missing/empty listings remain library errors, never coverage evidence.
        return super().discover_runs(  # type: ignore[misc]
            requirements, listings=self.catalog.listings, **kwargs,
        )

    def prepare_run(self, run: Any, requirements: Any, **kwargs: Any) -> Any:
        if self.catalog is None:
            raise LocalWeatherRequest(self.model)
        asset = next((item for item in self.catalog.assets if item.run == str(run)), None)
        if asset is None:
            raise LocalWeatherError(
                "WEATHER_PREPARED_UNAVAILABLE",
                f"選択した{self.model} Runの配信データがありません。",
            )
        if asset.sha256 not in self.payloads:
            raise LocalWeatherRequest(self.model, asset)
        return super().prepare_run(  # type: ignore[misc]
            run, requirements, prepared_data=self.payloads[asset.sha256], **kwargs,
        )


class _MsmClient(_PortableClient, MsmClient):  # type: ignore[misc]
    model: ForecastModel = "MSM"


class _GsmClient(_PortableClient, GsmClient):  # type: ignore[misc]
    model: ForecastModel = "GSM"


class LocalForecastWeather:
    def __init__(self) -> None:
        self.clients: dict[ForecastModel, _PortableClient] = {
            "MSM": _MsmClient(), "GSM": _GsmClient(),
        }
        self.provider = ForecastWeatherProvider({
            "MSM": MsmWeatherProvider("unused-browser-cache", client=self.clients["MSM"]),
            "GSM": GsmWeatherProvider("unused-browser-cache", client=self.clients["GSM"]),
        })

    def begin(self) -> None:
        # Once per user action, not per synchronous replay. Browser Cache API
        # remains the only warm-acquisition path; previous arrays cannot hide IO errors.
        for client in self.clients.values():
            client.clear()
        self.provider.begin_calculation()

    def catalog(self, model: ForecastModel, text: str) -> None:
        client = self.clients[model]
        client.clear()
        try:
            catalog = WeatherCatalog.model_validate_json(text)
        except ValueError as error:
            raise LocalWeatherError(
                "WEATHER_CATALOG_INVALID", f"{model}配信情報が破損しています。",
            ) from error
        now = datetime.now(UTC)
        if catalog.expires_at <= now or catalog.generated_at > now + CATALOG_CLOCK_SKEW:
            raise LocalWeatherError(
                "WEATHER_CATALOG_EXPIRED", f"{model}配信情報の有効期限を確認してください。",
            )
        client.catalog = catalog

    def accept(self, model: ForecastModel, payload: bytes, sha256: str) -> None:
        # Imported at the boundary so the pinned wheel is the sole codec source.
        from jma_gpv_weather import MsmPreparedData

        client = self.clients[model]
        client.payloads.pop(sha256, None)
        asset = next((item for item in client.catalog.assets if item.sha256 == sha256), None) \
            if client.catalog else None
        if asset is None:
            raise LocalWeatherError("WEATHER_CATALOG_INVALID", "予報取得計画がありません。")
        try:
            if len(payload) != asset.bytes:
                raise ValueError("prepared payload length differs from catalog")
            codec: Any = MsmPreparedData
            if model == "GSM":
                from jma_gpv_weather import GsmPreparedData

                codec = GsmPreparedData
            data = codec.from_bytes(payload, expected_sha256=sha256)
            if data.selection.run_utc.strftime("%Y%m%d%H%M%S") != asset.run:
                raise ValueError("prepared payload Run differs from catalog")
        except Exception as error:
            raise LocalWeatherError("WEATHER_PAYLOAD_INTEGRITY_FAILED", str(error)) from error
        client.payloads[sha256] = data
