from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonavlog.domain.enums import Availability, WeatherRequestKind
from autonavlog.domain.weather import ForecastRequirement, WeatherRequest
from autonavlog.weather.msm_fixture import fixture_weather_provider

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/msm"
OLD = "20260912000000"
NEW = "20260912030000"


def requirement(hour=4):
    return ForecastRequirement(
        valid_times_utc=(datetime(2026, 9, 12, hour, tzinfo=UTC),),
        require_surface_temperature=True,
    )


def test_actual_msm_run_selection_pinning_and_compatibility():
    provider = fixture_weather_provider(FIXTURE)
    assert provider.resolve_run(requirement()).id == NEW
    status = provider.inspect_run_status(OLD, requirement())
    assert status.selected_run_id == OLD
    assert status.latest_compatible_run_id == NEW
    assert status.selected_run_covers_requirement and status.update_available
    assert not provider.inspect_run_status(NEW, requirement()).update_available
    with pytest.raises(Exception, match="固定MSM fixture"):
        provider.resolve_run(requirement(7))
    missing = provider.inspect_run_status("20260911000000", requirement())
    assert not missing.selected_run_covers_requirement
    assert not missing.update_available
    with pytest.raises(Exception, match="outside fixture coverage"):
        provider.prepare_run("20260911000000", requirement())


def test_actual_msm_queries_are_run_specific_and_preserve_provenance():
    provider = fixture_weather_provider(FIXTURE)
    queries = [WeatherRequest(
        request_id=kind.value, kind=kind,
        latitude_deg=32.1, longitude_deg=131.2,
        valid_time_utc=requirement().valid_times_utc[0],
        altitude_ft_msl=6500, elevation_ft_msl=19,
    ) for kind in (WeatherRequestKind.ALOFT, WeatherRequestKind.SURFACE_TEMPERATURE)]
    with pytest.raises(RuntimeError, match="must be prepared"):
        provider.query_batch(OLD, queries)
    results = []
    for run in (OLD, NEW):
        provider.prepare_run(run, requirement())
        batch = provider.query_batch(run, queries)
        assert [item.request_id for item in batch] == [item.request_id for item in queries]
        assert all(item.availability == Availability.AVAILABLE for item in batch)
        assert all(item.metadata["provenance"]["source_hashes"] for item in batch)
        results.append(batch)
    assert results[0][0].values != results[1][0].values
    outside = queries[0].model_copy(update={"latitude_deg": 35})
    assert provider.query_batch(NEW, [outside])[0].availability == Availability.UNAVAILABLE


def test_corrupt_msm_fixture_fails_closed(tmp_path):
    import shutil
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    (tmp_path / f"{NEW}.npz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        fixture_weather_provider(tmp_path).prepare_run(NEW, requirement())
