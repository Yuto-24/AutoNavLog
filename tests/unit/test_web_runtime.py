from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import autonavlog.web.runtime as runtime
from autonavlog.web.runtime import WebRuntimeConfig


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


def test_msm_metar_delegate_receives_terrain_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class RecordingMsm:
        def __init__(self, *, cache_dir: Path, terrain_cache_path: Path | None) -> None:
            captured["cache_dir"] = cache_dir
            captured["terrain_cache_path"] = terrain_cache_path

    class RecordingMetar:
        def __init__(self, delegate: object) -> None:
            captured["delegate"] = delegate

    monkeypatch.setattr(runtime, "MsmWeatherProvider", RecordingMsm)
    monkeypatch.setattr(runtime, "MsmMetarWeatherProvider", RecordingMetar)
    config = WebRuntimeConfig(
        data_root=tmp_path / "data",
        storage_root=tmp_path / "storage",
        weather_mode="msm-metar",
        msm_cache_dir=tmp_path / "msm",
        terrain_cache_path=tmp_path / "terrain.json",
    )

    factory, label, development = runtime._weather_factory(config)
    provider = factory()

    assert isinstance(provider, RecordingMetar)
    assert isinstance(captured["delegate"], RecordingMsm)
    assert captured["cache_dir"] == tmp_path / "msm"
    assert captured["terrain_cache_path"] == tmp_path / "terrain.json"
    assert label == "MSM予報・METAR観測QNH"
    assert development is False
