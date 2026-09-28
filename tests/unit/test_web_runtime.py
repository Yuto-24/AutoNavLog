from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from threading import Event
from typing import Any

import pytest

import autonavlog.web.runtime as runtime
from autonavlog.domain.weather import ForecastRequirement, ForecastRun
from autonavlog.weather.prewarm import WeatherPrewarmer
from autonavlog.web.__main__ import _parser
from autonavlog.web.runtime import WebRuntimeConfig, environment_bool


def test_environment_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_BOOLEAN", raising=False)
    assert environment_bool("TEST_BOOLEAN", default=True) is True
    assert environment_bool("TEST_BOOLEAN", default=False) is False

    monkeypatch.setenv("TEST_BOOLEAN", "yes")
    assert environment_bool("TEST_BOOLEAN", default=False) is True
    monkeypatch.setenv("TEST_BOOLEAN", "OFF")
    assert environment_bool("TEST_BOOLEAN", default=True) is False

    monkeypatch.setenv("TEST_BOOLEAN", "invalid")
    with pytest.raises(RuntimeError, match="TEST_BOOLEAN must be true or false"):
        environment_bool("TEST_BOOLEAN", default=True)


def test_local_web_cli_binds_all_interfaces_by_default() -> None:
    arguments = _parser().parse_args([])

    assert arguments.host == "0.0.0.0"


def test_runtime_config_rejects_invalid_weather_and_session_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported weather mode"):
        WebRuntimeConfig(
            data_root=tmp_path,
            storage_root=tmp_path,
            weather_mode="invalid",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="maximum_sessions"):
        WebRuntimeConfig(
            data_root=tmp_path,
            storage_root=tmp_path,
            maximum_sessions=0,
        )
    with pytest.raises(ValueError, match="must be set together"):
        WebRuntimeConfig(
            data_root=tmp_path,
            storage_root=tmp_path,
            cloudflare_team_domain="https://test.cloudflareaccess.com",
        )
    with pytest.raises(ValueError, match="insecure session cookies"):
        WebRuntimeConfig(
            data_root=tmp_path,
            storage_root=tmp_path,
            session_cookie_secure=False,
        )
    with pytest.raises(ValueError, match="insecure session cookies"):
        WebRuntimeConfig(
            data_root=tmp_path,
            storage_root=tmp_path,
            trusted_local_identity="local-user",
            session_cookie_secure=False,
            cloudflare_team_domain="https://test.cloudflareaccess.com",
            cloudflare_access_audience="test-audience",
        )


def test_msm_provider_receives_cache_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class RecordingMsm:
        def __init__(self, *, cache_dir: Path) -> None:
            captured["cache_dir"] = cache_dir

    monkeypatch.setattr(runtime, "MsmWeatherProvider", RecordingMsm)
    config = WebRuntimeConfig(
        data_root=tmp_path / "data",
        storage_root=tmp_path / "storage",
        weather_mode="msm",
        msm_cache_dir=tmp_path / "msm",
    )

    factory, label, development = runtime._weather_factory(config)
    provider = factory()

    assert isinstance(provider, runtime.ForecastWeatherProvider)
    assert isinstance(provider.models["MSM"], RecordingMsm)
    other = factory()
    provider.selected_model = "GSM"
    assert other.selected_model == "MSM"
    assert provider.models["MSM"] is not other.models["MSM"]
    assert captured["cache_dir"] == tmp_path / "msm"
    assert label == "MSM / GSM予報"
    assert development is False


def test_weather_prewarmer_runs_cleanup_and_restarts_after_shutdown() -> None:
    class Provider:
        def __init__(self) -> None:
            self.prepared = Event()
            self.prepare_count = 0
            self.requirements: list[ForecastRequirement] = []

        def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
            return ForecastRun(
                id="20260812000000",
                initial_time_utc=requirement.valid_times_utc[0],
            )

        def prepare_run(
            self,
            forecast_run_id: str,
            requirement: ForecastRequirement,
        ) -> None:
            self.prepare_count += 1
            self.requirements.append(requirement)
            self.prepared.set()

    provider = Provider()
    cleaned = Event()
    prewarmer = WeatherPrewarmer(
        provider,  # type: ignore[arg-type]
        interval=timedelta(hours=1),
        cleanup=cleaned.set,
    )

    prewarmer.start()
    assert provider.prepared.wait(timeout=2)
    prewarmer.shutdown()
    assert cleaned.is_set()

    provider.prepared.clear()
    cleaned.clear()
    prewarmer.start()
    assert provider.prepared.wait(timeout=2)
    prewarmer.shutdown()

    assert cleaned.is_set()
    assert provider.prepare_count == 2
    assert all(
        requirement.require_surface_temperature
        for requirement in provider.requirements
    )


def test_prune_msm_cache_reports_deleted_count_and_remaining_bytes(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache_dir = tmp_path / "msm-cache"
    cache_dir.mkdir()
    cached = cache_dir / "fixture.grib2"
    cached.write_bytes(b"weather")

    with caplog.at_level("INFO", logger=runtime.__name__):
        deleted, remaining = runtime._prune_msm_cache(cache_dir, maximum_bytes=0)

    assert (deleted, remaining) == (1, 0)
    assert not cached.exists()
    assert "deleted_files=1 remaining_bytes=0" in caplog.text
