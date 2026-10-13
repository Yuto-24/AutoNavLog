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

    assert isinstance(provider, RecordingMsm)
    assert captured["cache_dir"] == tmp_path / "msm"
    assert label == "MSM予報"
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


@pytest.mark.parametrize("enabled", [False, True])
def test_msm_prewarm_switch_preserves_on_demand_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    prepared: list[ForecastRequirement] = []

    class RecordingMsm:
        def __init__(self, *, cache_dir: Path) -> None:
            pass

        def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
            return ForecastRun(id="20260812000000", initial_time_utc=requirement.valid_times_utc[0])

        def prepare_run(self, run_id: str, requirement: ForecastRequirement) -> None:
            prepared.append(requirement)

    monkeypatch.setattr(runtime, "MsmWeatherProvider", RecordingMsm)
    app = runtime.build_web_application(WebRuntimeConfig(
        data_root=Path(__file__).resolve().parents[2] / "data",
        storage_root=tmp_path,
        weather_mode="msm",
        msm_prewarm=enabled,
    ))
    assert prepared == []
    assert isinstance(app.weather_factory(), RecordingMsm)
    assert app.weather_factory() is app.weather_factory()
    if enabled:
        assert app.weather_prewarmer is not None
        app.weather_prewarmer.run_once()
        assert len(prepared) == 1
        times = prepared[0].valid_times_utc
        assert max(times) - min(times) == timedelta(hours=48)
    else:
        assert app.weather_prewarmer is None
        # The same provider is still available to calculation requests.
        from datetime import UTC, datetime
        requirement = ForecastRequirement(valid_times_utc=(datetime.now(UTC),))
        provider = app.weather_factory()
        provider.prepare_run(provider.resolve_run(requirement).id, requirement)
        assert prepared == [requirement]


def test_cli_reads_prewarm_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autonavlog.web import __main__ as cli

    configs = []
    monkeypatch.setattr("sys.argv", ["autonavlog-web", "--storage-root", str(tmp_path)])
    monkeypatch.setattr(cli, "create_app", lambda config: configs.append(config))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.delenv("AUTONAVLOG_MSM_PREWARM", raising=False)
    cli.main()
    assert configs[-1].msm_prewarm is True
    monkeypatch.setenv("AUTONAVLOG_MSM_PREWARM", "false")
    cli.main()
    assert configs[-1].msm_prewarm is False
    monkeypatch.setenv("AUTONAVLOG_MSM_PREWARM", "true")
    cli.main()
    assert configs[-1].msm_prewarm is True
    monkeypatch.setenv("AUTONAVLOG_MSM_PREWARM", "typo")
    with pytest.raises(RuntimeError, match="AUTONAVLOG_MSM_PREWARM"):
        cli.main()


@pytest.mark.parametrize("value, expected", [(None, True), ("false", False), ("true", True)])
def test_environment_factory_reads_prewarm_switch(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool,
) -> None:
    from autonavlog.web.app import _environment_config

    monkeypatch.setenv("AUTONAVLOG_WEATHER", "msm")
    if value is None:
        monkeypatch.delenv("AUTONAVLOG_MSM_PREWARM", raising=False)
    else:
        monkeypatch.setenv("AUTONAVLOG_MSM_PREWARM", value)
    assert _environment_config().msm_prewarm is expected


def test_environment_factory_rejects_invalid_prewarm_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autonavlog.web.app import _environment_config

    monkeypatch.setenv("AUTONAVLOG_MSM_PREWARM", "typo")
    with pytest.raises(RuntimeError, match="AUTONAVLOG_MSM_PREWARM"):
        _environment_config()


@pytest.mark.parametrize("enabled", [False, True])
def test_environment_factory_lifespan_only_starts_enabled_prewarm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool,
) -> None:
    from fastapi.testclient import TestClient

    from autonavlog.web.app import create_app

    started = Event()
    monkeypatch.setenv("AUTONAVLOG_WEATHER", "msm")
    monkeypatch.setenv("AUTONAVLOG_MSM_PREWARM", str(enabled).lower())
    monkeypatch.setenv("AUTONAVLOG_DATA_ROOT", str(Path(__file__).resolve().parents[2] / "data"))
    monkeypatch.setenv("AUTONAVLOG_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(WeatherPrewarmer, "run_once", lambda self: started.set())
    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200
        if enabled:
            assert started.wait(2)
        else:
            assert not started.is_set()
    assert started.is_set() is enabled
