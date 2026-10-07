from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from jma_gpv_weather import Availability as LibraryAvailability

from autonavlog.application.forecast_selection import select_forecast
from autonavlog.domain.enums import WeatherRequestKind
from autonavlog.domain.weather import ForecastCoverageError, ForecastRequirement, WeatherRequest
from autonavlog.weather.gsm_adapter import GsmWeatherProvider
from autonavlog.weather.msm_adapter import MsmWeatherProvider

OLD, NEW = "20260915000000", "20260915120000"


@pytest.fixture
def requirement():
    time = datetime(2026, 9, 16, tzinfo=UTC)
    return ForecastRequirement(
        valid_times_utc=(time,),
        require_surface_temperature=True,
        coverage_requests=(
            WeatherRequest(request_id="aloft", kind=WeatherRequestKind.ALOFT,
                           latitude_deg=32, longitude_deg=131, valid_time_utc=time,
                           altitude_ft_msl=6500),
            WeatherRequest(request_id="surface", kind=WeatherRequestKind.SURFACE_TEMPERATURE,
                           latitude_deg=32, longitude_deg=131, valid_time_utc=time),
        ),
    )


class Client:
    def __init__(self, *, altitude=None, query_error=None, discovery_error=None,
                 outside=False):
        self.altitude = altitude or {}
        self.query_error = query_error
        self.discovery_error = discovery_error
        self.outside = outside
        self.prepared = []
        self.points = ()

    def check_coverage(self, requirement, *, run=None, points=()):
        self.points = points
        return SimpleNamespace(outside_spec=self.outside,
                               state="outside_spec" if self.outside else "requires_hgt",
                               reason_codes=("OUTSIDE_DOMAIN",) if self.outside else
                               ("MSL_ALTITUDE_REQUIRES_HGT",))

    def discover_runs(self, requirement):
        if self.discovery_error:
            raise RuntimeError(self.discovery_error)
        return tuple(SimpleNamespace(run_utc=datetime.strptime(run, "%Y%m%d%H%M%S")
                                     .replace(tzinfo=UTC)) for run in (NEW, OLD))

    def prepare_run(self, run, requirement, **kwargs):
        assert len(kwargs["available_runs"]) == 2
        self.prepared.append(str(run))
        return SimpleNamespace(
            check_altitude_coverage=lambda q: SimpleNamespace(
                availability=LibraryAvailability.UNAVAILABLE if str(run) in self.altitude
                else LibraryAvailability.AVAILABLE,
                reason_code=self.altitude.get(str(run)), provenance={}),
            query_many=lambda qs: [SimpleNamespace(
                availability=LibraryAvailability.UNAVAILABLE if self.query_error
                else LibraryAvailability.AVAILABLE,
                reason_code=self.query_error, values={"temperature_k": 280},
                warnings=(), provenance={}) for q in qs],
        )


def test_requires_hgt_uses_public_post_prepare_check(requirement, tmp_path):
    client = Client()
    provider = MsmWeatherProvider(tmp_path, client)
    provider.check_run(NEW, requirement)
    assert client.prepared == [NEW]
    assert client.points[0].altitude_msl_m == 6500 * 0.3048
    assert client.points[1].altitude_msl_m is None


def test_latest_altitude_exclusion_selects_older_msm_not_gsm(requirement, tmp_path):
    msm = Client(altitude={NEW: "ALTITUDE_OUTSIDE_HGT_RANGE"})
    gsm = Client()
    result = select_forecast({"MSM": MsmWeatherProvider(tmp_path, msm),
                              "GSM": GsmWeatherProvider(tmp_path, gsm)}, requirement)
    assert (result.model, result.run_id) == ("MSM", OLD)
    assert msm.prepared == [NEW, OLD]
    assert gsm.prepared == []


@pytest.mark.parametrize("reason", ["SOURCE_VALUE_UNAVAILABLE", "OUTSIDE_PREPARED_AREA",
                                    "TIME_UNAVAILABLE", "UNEXPECTED"])
def test_other_post_prepare_reasons_are_processing_errors(requirement, tmp_path, reason):
    client = Client(altitude={NEW: reason})
    with pytest.raises(RuntimeError, match=reason):
        MsmWeatherProvider(tmp_path, client).check_run(NEW, requirement)


def test_missing_other_variable_overrides_altitude_exclusion(requirement, tmp_path):
    client = Client(altitude={NEW: "ALTITUDE_OUTSIDE_HGT_RANGE"},
                    query_error="SOURCE_VALUE_UNAVAILABLE")
    with pytest.raises(RuntimeError, match="SOURCE_VALUE_UNAVAILABLE"):
        MsmWeatherProvider(tmp_path, client).check_run(NEW, requirement)


def test_offline_exclusion_does_not_prepare_or_discover(requirement, tmp_path):
    client = Client(outside=True, discovery_error="must not discover")
    with pytest.raises(ForecastCoverageError, match="OUTSIDE_DOMAIN"):
        MsmWeatherProvider(tmp_path, client).candidate_runs(requirement)
    assert client.prepared == []


def test_gsm_uses_public_gsm_client_without_msm_terrain_argument(requirement, tmp_path):
    class GsmClient(Client):
        def prepare_run(self, run, requirement, *, available_runs):
            return super().prepare_run(run, requirement, available_runs=available_runs)

    client = GsmClient()
    GsmWeatherProvider(tmp_path, client).check_run(NEW, requirement)
    assert client.prepared == [NEW]


def test_desktop_msm_listing_error_is_not_swallowed(requirement, tmp_path):
    provider = MsmWeatherProvider(tmp_path)
    urls = []

    def fail(url):
        urls.append(url)
        raise RuntimeError("listing failed")

    provider.client.source = SimpleNamespace(
        directory_url=lambda day: f"https://example.invalid/{day}", read_listing=fail,
    )
    with pytest.raises(RuntimeError, match="listing failed"):
        provider.candidate_runs(requirement)
    assert len(urls) == 1


def test_actual_weather_sample_times_are_part_of_public_requirement(requirement, tmp_path):
    from datetime import timedelta
    later = requirement.valid_times_utc[-1] + timedelta(hours=4)
    expanded = requirement.model_copy(update={
        "coverage_requests": (requirement.coverage_requests[0].model_copy(
            update={"valid_time_utc": later}),),
    })
    native = MsmWeatherProvider(tmp_path, Client())._requirement(expanded)
    assert later in native.valid_times
    assert requirement.valid_times_utc[0] in native.valid_times


@pytest.mark.parametrize("provider_type,latitude", [(MsmWeatherProvider, 45),
                                                  (GsmWeatherProvider, 20.5)])
def test_native_prepare_crops_to_complete_request_without_copying_model_geometry(
    provider_type, latitude, requirement, tmp_path, monkeypatch,
):
    provider = provider_type(tmp_path)
    original = provider.client.bounds
    request = requirement.coverage_requests[0].model_copy(update={"latitude_deg": latitude})
    extended = requirement.model_copy(update={"coverage_requests": (request,)})
    fake = Client()
    runs = fake.discover_runs(provider._requirement(extended))
    monkeypatch.setattr(provider, "_discover_candidates", lambda req: runs)
    seen = []

    def prepare(run, native, **kwargs):
        seen.append(provider.client.bounds)
        return fake.prepare_run(run, native, **kwargs)

    monkeypatch.setattr(provider.client, "prepare_run", prepare)
    provider.check_run(NEW, extended)
    assert seen[-1].lat_min <= latitude <= seen[-1].lat_max
    assert seen[-1].lon_min <= request.longitude_deg <= seen[-1].lon_max
    provider.begin_calculation()
    assert provider.client.bounds == original
