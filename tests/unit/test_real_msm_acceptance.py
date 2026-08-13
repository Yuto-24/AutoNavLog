from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Any

import pytest

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import (
    ForecastRun,
    PreparedForecastRun,
    RunSelectionStatus,
    WeatherRequest,
    WeatherResult,
)
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.msm_adapter import MsmWeatherProvider
from autonavlog.weather.real_msm_acceptance import (
    RealMsmAcceptanceConfig,
    RealMsmAcceptanceError,
    run_real_msm_acceptance,
)

VALID_TIME = datetime(2026, 7, 30, 3, tzinfo=timezone.utc)
RUN_TIME = datetime(2026, 7, 29, 18, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64
TERRAIN_SHA = "c" * 64
SOURCE_URLS = (
    "http://database.rish.kyoto-u.ac.jp/arch/jmadata/a.bin",
    "http://database.rish.kyoto-u.ac.jp/arch/jmadata/b.bin",
)


class StubTerrain:
    source = "JMA MSM model terrain (Pzs)"
    source_sha256 = TERRAIN_SHA
    values = {"RJFM": 11.0, "RJFO": 19.0}

    def __call__(self, latitude: float, longitude: float) -> float | None:
        if latitude < 32:
            return self.values["RJFM"]
        return self.values["RJFO"]


class StubTerrainType:
    loaded_paths: list[Path] = []

    @classmethod
    def load(cls, path: Path) -> StubTerrain:
        cls.loaded_paths.append(path)
        return StubTerrain()


def _provenance(*, qnh: bool) -> dict[str, Any]:
    trace: dict[str, Any]
    if qnh:
        trace = {
            "terrain_source": "JMA MSM model terrain (Pzs)",
            "terrain_source_sha256": TERRAIN_SHA,
        }
    else:
        trace = {"u": [{"grid": "fixture"}], "v": [{"grid": "fixture"}]}
    return {
        "initial_time_utc": RUN_TIME.isoformat(),
        "source_urls": list(SOURCE_URLS),
        "source_hashes": {SOURCE_URLS[0]: SHA_A, SOURCE_URLS[1]: SHA_B},
        "interpolation_method": (
            "bilinear,time-linear,hypsometric-isa-v1"
            if qnh
            else "vertical-linear,bilinear,time-linear"
        ),
        "trace": trace,
    }


def _weather_result(request: WeatherRequest) -> WeatherResult:
    if request.kind == WeatherRequestKind.ESTIMATED_QNH:
        return WeatherResult(
            request_id=request.request_id,
            availability=Availability.AVAILABLE,
            kind=request.kind,
            values={"label": "MSM推定QNH", "qnh_hpa": 1009.4},
            warnings=("NOT_FOR_OPERATIONAL_USE",),
            metadata={"provenance": _provenance(qnh=True)},
        )
    return WeatherResult(
        request_id=request.request_id,
        availability=Availability.AVAILABLE,
        kind=request.kind,
        values={
            "u_ms": 2.0,
            "v_ms": -3.0,
            "wind_speed_kt": 7.0,
            "temperature_c": 4.0,
        },
        metadata={"provenance": _provenance(qnh=False)},
    )


def _real_provider_stub(terrain_path: Path) -> tuple[MsmWeatherProvider, dict[str, int]]:
    provider = MsmWeatherProvider.__new__(MsmWeatherProvider)
    provider.terrain_cache_path = terrain_path
    provider._msm = SimpleNamespace(
        __version__="0.2.1",
        GridTerrainProvider=StubTerrainType,
    )
    calls = {"resolve": 0, "inspect": 0, "prepare": 0, "query": 0}

    def resolve(self: MsmWeatherProvider, requirement: Any) -> ForecastRun:
        calls["resolve"] += 1
        return ForecastRun(id="20260729180000", initial_time_utc=RUN_TIME)

    def inspect(
        self: MsmWeatherProvider,
        selected_run_id: str,
        requirement: Any,
    ) -> RunSelectionStatus:
        calls["inspect"] += 1
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=selected_run_id,
            selected_run_covers_requirement=True,
        )

    def prepare(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requirement: Any,
    ) -> PreparedForecastRun:
        calls["prepare"] += 1
        return PreparedForecastRun(
            forecast_run_id=forecast_run_id,
            requirement=requirement,
            metadata={"provider": "jma-msm-wind", "package_version": "0.2.1"},
        )

    def query(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        calls["query"] += 1
        return tuple(_weather_result(request) for request in requests)

    provider.resolve_run = MethodType(resolve, provider)
    provider.inspect_run_status = MethodType(inspect, provider)
    provider.prepare_run = MethodType(prepare, provider)
    provider.query_batch = MethodType(query, provider)
    return provider, calls


@pytest.fixture
def terrain_path(tmp_path: Path) -> Path:
    path = tmp_path / "terrain.npz"
    path.write_bytes(b"stub terrain artifact")
    return path


@pytest.fixture(autouse=True)
def pinned_distribution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "autonavlog.weather.real_msm_acceptance.metadata.version",
        lambda distribution: "0.2.1",
    )


def test_fake_provider_cannot_pass_real_msm_gate(tmp_path: Path) -> None:
    with pytest.raises(RealMsmAcceptanceError, match="exact MsmWeatherProvider"):
        run_real_msm_acceptance(
            FakeWeatherProvider(),
            RealMsmAcceptanceConfig(terrain_cache_path=tmp_path / "terrain.npz"),
        )


def test_offline_preflight_checks_artifacts_without_weather_calls(
    terrain_path: Path,
) -> None:
    provider, calls = _real_provider_stub(terrain_path)
    report = run_real_msm_acceptance(
        provider,
        RealMsmAcceptanceConfig(terrain_cache_path=terrain_path),
    )
    assert report["status"] == "PASS"
    assert report["mode"] == "OFFLINE_PREFLIGHT"
    assert report["live_executed"] is False
    assert report["terrain"]["samples_m"] == {"RJFM": 11.0, "RJFO": 19.0}
    assert calls == {"resolve": 0, "inspect": 0, "prepare": 0, "query": 0}


def test_preflight_fails_when_required_terrain_is_absent(tmp_path: Path) -> None:
    terrain_path = tmp_path / "missing.npz"
    provider, _ = _real_provider_stub(terrain_path)
    with pytest.raises(RealMsmAcceptanceError, match="terrain cache is absent"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(terrain_cache_path=terrain_path),
        )


def test_preflight_fails_when_package_version_is_not_pinned(
    monkeypatch: pytest.MonkeyPatch,
    terrain_path: Path,
) -> None:
    monkeypatch.setattr(
        "autonavlog.weather.real_msm_acceptance.metadata.version",
        lambda distribution: "0.2.2",
    )
    provider, _ = _real_provider_stub(terrain_path)
    with pytest.raises(RealMsmAcceptanceError, match="0.2.1 exactly"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(terrain_cache_path=terrain_path),
        )


def test_preflight_fails_when_imported_module_version_is_not_pinned(
    terrain_path: Path,
) -> None:
    provider, _ = _real_provider_stub(terrain_path)
    provider._msm.__version__ = "0.2.0"
    with pytest.raises(RealMsmAcceptanceError, match="0.2.1 exactly"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(terrain_cache_path=terrain_path),
        )


def test_preflight_fails_for_nonfinite_airport_terrain(terrain_path: Path) -> None:
    provider, _ = _real_provider_stub(terrain_path)
    StubTerrain.values["RJFO"] = float("nan")
    try:
        with pytest.raises(RealMsmAcceptanceError, match="Pzs terrain at RJFO must be finite"):
            run_real_msm_acceptance(
                provider,
                RealMsmAcceptanceConfig(terrain_cache_path=terrain_path),
            )
    finally:
        StubTerrain.values["RJFO"] = 19.0


def test_live_gate_resolves_prepares_queries_and_validates_provenance(
    terrain_path: Path,
) -> None:
    provider, calls = _real_provider_stub(terrain_path)
    report = run_real_msm_acceptance(
        provider,
        RealMsmAcceptanceConfig(
            terrain_cache_path=terrain_path,
            valid_time_utc=VALID_TIME,
            live=True,
        ),
    )
    assert report["status"] == "PASS"
    assert report["mode"] == "LIVE"
    assert report["live_executed"] is True
    assert report["forecast"]["result_count"] == 4
    assert {result["kind"] for result in report["forecast"]["results"]} == {
        "ALOFT",
        "ESTIMATED_QNH",
    }
    assert calls == {"resolve": 1, "inspect": 1, "prepare": 1, "query": 1}


def test_live_gate_fails_instead_of_skipping_unavailable_result(
    terrain_path: Path,
) -> None:
    provider, _ = _real_provider_stub(terrain_path)

    def unavailable(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        request = requests[0]
        results[0] = WeatherResult(
            request_id=request.request_id,
            availability=Availability.UNAVAILABLE,
            kind=request.kind,
            reason_code="NO_DATA",
        )
        return tuple(results)

    provider.query_batch = MethodType(unavailable, provider)
    with pytest.raises(RealMsmAcceptanceError, match="not AVAILABLE"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                terrain_cache_path=terrain_path,
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )


def test_live_gate_fails_when_source_provenance_is_missing(terrain_path: Path) -> None:
    provider, _ = _real_provider_stub(terrain_path)

    def missing_provenance(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        first = results[0]
        results[0] = first.model_copy(update={"metadata": {"provenance": {}}})
        return tuple(results)

    provider.query_batch = MethodType(missing_provenance, provider)
    with pytest.raises(RealMsmAcceptanceError, match="source_urls"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                terrain_cache_path=terrain_path,
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )


def test_live_gate_fails_when_source_urls_and_hashes_do_not_match(
    terrain_path: Path,
) -> None:
    provider, _ = _real_provider_stub(terrain_path)

    def mismatched_source_hashes(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        first = results[0]
        provenance = dict(first.metadata["provenance"])
        provenance["source_hashes"] = {SOURCE_URLS[0]: SHA_A}
        results[0] = first.model_copy(update={"metadata": {"provenance": provenance}})
        return tuple(results)

    provider.query_batch = MethodType(mismatched_source_hashes, provider)
    with pytest.raises(RealMsmAcceptanceError, match="source URL/hash key mismatch"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                terrain_cache_path=terrain_path,
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )


def test_live_gate_fails_when_qnh_uses_a_different_terrain_source(
    terrain_path: Path,
) -> None:
    provider, _ = _real_provider_stub(terrain_path)

    def mismatched_terrain_hash(
        self: MsmWeatherProvider,
        forecast_run_id: str,
        requests: Any,
    ) -> tuple[WeatherResult, ...]:
        results = [_weather_result(request) for request in requests]
        qnh_index = next(
            index
            for index, request in enumerate(requests)
            if request.kind == WeatherRequestKind.ESTIMATED_QNH
        )
        qnh = results[qnh_index]
        provenance = dict(qnh.metadata["provenance"])
        trace = dict(provenance["trace"])
        trace["terrain_source_sha256"] = "d" * 64
        provenance["trace"] = trace
        results[qnh_index] = qnh.model_copy(update={"metadata": {"provenance": provenance}})
        return tuple(results)

    provider.query_batch = MethodType(mismatched_terrain_hash, provider)
    with pytest.raises(RealMsmAcceptanceError, match="does not match preflight"):
        run_real_msm_acceptance(
            provider,
            RealMsmAcceptanceConfig(
                terrain_cache_path=terrain_path,
                valid_time_utc=VALID_TIME,
                live=True,
            ),
        )
