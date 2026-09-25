from datetime import UTC, datetime

import pytest

from autonavlog.application.forecast_selection import (
    ForecastCoverageError,
    ForecastUnavailable,
    select_forecast,
)
from autonavlog.domain.weather import ForecastRequirement

OLD, NEW = "20260915000000", "20260915120000"
REQUIREMENT = ForecastRequirement(valid_times_utc=(datetime(2026, 9, 16, tzinfo=UTC),))


@pytest.mark.parametrize("reasons", [(), ("",), (" ",)])
def test_coverage_exclusion_requires_evidence(reasons):
    with pytest.raises(ValueError, match="requires reason codes"):
        ForecastCoverageError(reasons)


class CandidateProvider:
    def __init__(self, runs=(NEW, OLD), excluded=(), failure=None):
        self.runs, self.excluded, self.failure = runs, excluded, failure
        self.checked = []

    def candidate_runs(self, requirement):
        assert requirement == REQUIREMENT
        if isinstance(self.runs, Exception):
            raise self.runs
        return self.runs

    def check_run(self, run, requirement):
        assert requirement == REQUIREMENT
        self.checked.append(run)
        if self.failure:
            raise self.failure
        if run in self.excluded:
            raise ForecastCoverageError(("ALTITUDE_OUTSIDE_HGT_RANGE",))


def providers(msm=None, gsm=None):
    return {"MSM": msm or CandidateProvider(), "GSM": gsm or CandidateProvider()}


def test_msm_priority_and_latest_actually_covered_run():
    data = providers(msm=CandidateProvider(excluded=(NEW,)))
    selected = select_forecast(data, REQUIREMENT)
    assert (selected.model, selected.run_id) == ("MSM", OLD)
    assert data["MSM"].checked == [NEW, OLD]
    assert data["GSM"].checked == []


def test_all_msm_runs_outside_hgt_fall_back_with_provenance():
    data = providers(msm=CandidateProvider(excluded=(NEW, OLD)))
    selected = select_forecast(data, REQUIREMENT)
    assert (selected.model, selected.run_id) == ("GSM", NEW)
    assert selected.fallback_from == "MSM"
    assert selected.coverage_reason_codes == ("ALTITUDE_OUTSIDE_HGT_RANGE",)


def test_both_models_outside_coverage_are_unavailable():
    data = providers(CandidateProvider(excluded=(NEW, OLD)),
                     CandidateProvider(excluded=(NEW, OLD)))
    with pytest.raises(ForecastUnavailable):
        select_forecast(data, REQUIREMENT)


@pytest.mark.parametrize("reason", ["network", "listing", "download", "decode", "cache",
                                    "SOURCE_VALUE_UNAVAILABLE", "implementation"])
def test_processing_failures_never_try_older_run_or_other_model(reason):
    data = providers(msm=CandidateProvider(failure=RuntimeError(reason)))
    with pytest.raises(RuntimeError, match=reason):
        select_forecast(data, REQUIREMENT)
    assert data["MSM"].checked == [NEW]
    assert data["GSM"].checked == []


def test_empty_discovery_is_not_model_exclusion():
    data = providers(msm=CandidateProvider(runs=()))
    with pytest.raises(RuntimeError, match="without coverage evidence"):
        select_forecast(data, REQUIREMENT)
    assert data["GSM"].checked == []


def test_offline_model_exclusion_can_fall_back_without_preparing():
    data = providers(msm=CandidateProvider(runs=ForecastCoverageError(("OUTSIDE_DOMAIN",))))
    assert select_forecast(data, REQUIREMENT).model == "GSM"
    assert data["MSM"].checked == []


@pytest.mark.parametrize("fixed_excluded", [False, True])
def test_fixed_msm_with_another_msm_requires_explicit_refresh(fixed_excluded):
    data = providers(msm=CandidateProvider(excluded=(OLD,) if fixed_excluded else ()))
    first = select_forecast(data, REQUIREMENT, selected_model="MSM", selected_run_id=OLD)
    assert (first.model, first.run_id) == ("MSM", OLD)
    assert first.update_available
    assert first.update_required == fixed_excluded
    assert data["GSM"].checked == []
    second = select_forecast(data, REQUIREMENT, selected_model="MSM", selected_run_id=OLD,
                             refresh=True)
    assert second.run_id == NEW


def test_fixed_msm_model_exclusion_falls_back_immediately():
    data = providers(msm=CandidateProvider(excluded=(NEW, OLD)))
    selected = select_forecast(data, REQUIREMENT, selected_model="MSM", selected_run_id=OLD)
    assert (selected.model, selected.run_id) == ("GSM", NEW)
    assert not selected.update_required
    assert data["MSM"].checked.count(OLD) == 1


@pytest.mark.parametrize("fixed_excluded", [False, True])
def test_fixed_gsm_never_returns_to_msm_without_explicit_refresh(fixed_excluded):
    data = providers(gsm=CandidateProvider(excluded=(OLD,) if fixed_excluded else ()))
    first = select_forecast(data, REQUIREMENT, selected_model="GSM", selected_run_id=OLD)
    assert (first.model, first.run_id) == ("GSM", OLD)
    assert first.update_available
    assert first.update_required == fixed_excluded
    second = select_forecast(data, REQUIREMENT, selected_model="GSM", selected_run_id=OLD,
                             refresh=True)
    assert (second.model, second.run_id) == ("MSM", NEW)
