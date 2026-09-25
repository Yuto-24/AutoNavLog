"""Whole NAV LOG restarts use one model and preserve forecast failure semantics."""
from autonavlog.application.calculation_service import CalculationService
from autonavlog.domain.weather import ForecastCoverageError
from autonavlog.weather.fake_provider import FakeWeatherProvider
from autonavlog.weather.forecast_provider import ForecastWeatherProvider

OLD, NEW = "20260728000000", "20260728030000"


class Candidates(FakeWeatherProvider):
    def __init__(self, model, *, exclude=lambda run, req: False, fail=None):
        super().__init__(runs=(NEW, OLD))
        self.model, self.exclude, self.fail = model, exclude, fail
        self.checked = []

    def candidate_runs(self, requirement):
        return self.runs

    def check_run(self, run, requirement):
        self.checked.append((run, requirement))
        if self.fail:
            raise RuntimeError(self.fail)
        if self.exclude(run, requirement):
            raise ForecastCoverageError(("ALTITUDE_OUTSIDE_HGT_RANGE",))
        self.prepare_run(run, requirement)

    def query_batch(self, run, requests):
        return [r.model_copy(update={"metadata": {"model": self.model, "forecast_run_id": run}})
                for r in super().query_batch(run, requests)]


def test_whole_calculation_uses_fallback_model(airports, performance_repository, project):
    msm = Candidates("MSM", exclude=lambda run, req: True)
    gsm = Candidates("GSM")
    service = CalculationService(airports, performance_repository)
    result = service.calculate(project, ForecastWeatherProvider({"MSM": msm, "GSM": gsm}))
    assert result.sections and result.converged
    assert (result.selected_forecast_model, result.selected_forecast_run_id) == ("GSM", NEW)
    assert result.forecast_provenance["fallback_from"] == "MSM"
    assert result.forecast_provenance["coverage_reason_codes"] == ["ALTITUDE_OUTSIDE_HGT_RANGE"]
    assert not msm.query_history
    assert all(r.metadata["model"] == "GSM" for r in service.last_weather_results)
    assert not any("FALLBACK" in i.code for i in result.issues)


def test_processing_blocks_before_gsm(airports, performance_repository, project):
    msm, gsm = Candidates("MSM", fail="SOURCE_VALUE_UNAVAILABLE"), Candidates("GSM")
    result = CalculationService(airports, performance_repository).calculate(
        project, ForecastWeatherProvider({"MSM": msm, "GSM": gsm}))
    assert not result.sections
    assert [i.code for i in result.blockers] == ["FORECAST_PREPARE_FAILED"]
    assert not gsm.checked and not msm.query_history


def test_pinned_msm_excluded_requires_refresh_when_other_msm_covers(
    airports, performance_repository, project,
):
    project.selected_forecast_model, project.selected_forecast_run_id = "MSM", OLD
    msm, gsm = Candidates("MSM", exclude=lambda run, req: run == OLD), Candidates("GSM")
    service = CalculationService(airports, performance_repository)
    provider = ForecastWeatherProvider({"MSM": msm, "GSM": gsm})
    first = service.calculate(project, provider)
    assert not first.sections
    assert "FORECAST_UPDATE_REQUIRED" in [i.code for i in first.blockers]
    assert not gsm.checked
    second = service.calculate(project, provider, refresh_forecast=True)
    assert second.sections
    assert (second.selected_forecast_model, second.selected_forecast_run_id) == ("MSM", NEW)


def test_iteration_coverage_restarts_entire_calculation(
    airports, performance_repository, project,
):
    msm = Candidates("MSM")
    # Initial preparation and first query work. The next actual weather sample
    # proves the current model cannot cover this calculation's evolved request.
    msm.exclude = lambda run, req: bool(msm.query_history)
    gsm = Candidates("GSM")
    service = CalculationService(airports, performance_repository)
    result = service.calculate(project, ForecastWeatherProvider({"MSM": msm, "GSM": gsm}))
    assert msm.query_history and gsm.query_history
    assert result.sections and result.selected_forecast_model == "GSM"
    assert result.forecast_provenance["fallback"]
    assert all(r.metadata["model"] == "GSM" for r in service.last_weather_results)


def _local_forecast_session():
    from pathlib import Path

    from test_local_calculation import calculate

    from autonavlog.local import LocalApplication

    root = Path(__file__).resolve().parents[2]
    local = LocalApplication(root / "data", forecast_fixture=root / "tests/fixtures/msm")
    msm, gsm = Candidates("MSM"), Candidates("GSM")
    local.session.weather_provider = ForecastWeatherProvider({"MSM": msm, "GSM": gsm})
    calculate(local, forecast=True)
    return local, msm, gsm


def test_facade_two_click_refresh_and_input_change_discards_intent():
    import json

    local, msm, gsm = _local_forecast_session()
    try:
        local.session.project.selected_forecast_run_id = OLD
        first = json.loads(local.dispatch("calculate"))
        assert first["outcome"]["selected_forecast_run_id"] == OLD
        assert local.session.forecast_update_fingerprint is not None
        # Every application presentation observes mutations, even away and back.
        before = local.session.project.descent_rate_fpm
        local.session.project.descent_rate_fpm = 1000 if before == 500 else 500
        local.app.present(local.session)
        local.session.project.descent_rate_fpm = before
        local.app.present(local.session)
        assert local.session.forecast_update_fingerprint is None
        again = json.loads(local.dispatch("calculate"))
        assert again["outcome"]["selected_forecast_run_id"] == OLD
        second = json.loads(local.dispatch("calculate"))
        assert second["outcome"]["selected_forecast_run_id"] == NEW
        assert local.session.forecast_update_fingerprint is None
        assert not gsm.checked
    finally:
        local.close()


def test_facade_update_wait_and_processing_preserve_last_good():
    import json

    local, msm, gsm = _local_forecast_session()
    try:
        assert local.session.last_calculation is not None
        last = local.session.last_calculation.model_dump(mode="json")
        local.session.project.selected_forecast_run_id = OLD
        msm.exclude = lambda run, req: run == OLD
        result = json.loads(local.dispatch("calculate"))
        assert "FORECAST_UPDATE_REQUIRED" in [i["code"] for i in result["outcome"]["issues"]]
        assert local.session.last_calculation.model_dump(mode="json") == last
        msm.fail = "decode failure"
        json.loads(local.dispatch("calculate"))
        assert local.session.last_calculation.model_dump(mode="json") == last
        assert local.session.forecast_update_fingerprint is None
        assert not gsm.checked
    finally:
        local.close()


def test_final_requirement_with_other_msm_waits_without_adopting_partial_results(
    airports, performance_repository, project,
):
    project.selected_forecast_model, project.selected_forecast_run_id = "MSM", OLD
    msm, gsm = Candidates("MSM"), Candidates("GSM")
    msm.exclude = lambda run, req: run == OLD and bool(msm.query_history)
    service = CalculationService(airports, performance_repository)
    result = service.calculate(project, ForecastWeatherProvider({"MSM": msm, "GSM": gsm}))
    assert not result.sections
    assert "FORECAST_UPDATE_REQUIRED" in [i.code for i in result.blockers]
    assert result.selected_forecast_run_id == OLD
    assert not gsm.checked


def test_facade_both_models_excluded_preserves_last_good():
    import json

    local, msm, gsm = _local_forecast_session()
    try:
        last = local.session.last_calculation.model_dump(mode="json")
        msm.exclude = gsm.exclude = lambda run, req: True
        state = json.loads(local.dispatch("calculate"))
        assert "FORECAST_UNAVAILABLE" in [i["code"] for i in state["outcome"]["issues"]]
        assert local.session.last_calculation.model_dump(mode="json") == last
        assert local.session.forecast_update_fingerprint is None
    finally:
        local.close()
