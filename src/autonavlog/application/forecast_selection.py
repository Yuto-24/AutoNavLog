"""Application-owned model priority and fixed Forecast update policy.

Adapters evaluate complete requirements through the weather library. Only their
explicit coverage result permits trying another Run/model; acquisition and
processing exceptions propagate unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from autonavlog.domain.weather import ForecastCoverageError, ForecastModel, ForecastRequirement
from autonavlog.weather.provider import ForecastCandidateProvider


class ForecastUnavailable(ForecastCoverageError):
    """Both models were evaluated successfully and neither covers the request."""


@dataclass(frozen=True)
class ForecastSelection:
    model: ForecastModel
    run_id: str
    update_available: bool = False
    update_required: bool = False
    fallback_from: ForecastModel | None = None
    coverage_reason_codes: tuple[str, ...] = ()


def select_forecast(
    providers: Mapping[ForecastModel, ForecastCandidateProvider],
    requirement: ForecastRequirement,
    *,
    selected_model: ForecastModel | None = None,
    selected_run_id: str | None = None,
    refresh: bool = False,
) -> ForecastSelection:
    # One evaluation per candidate per selection. In particular, an out-of-HGT
    # fixed Run is not downloaded again while checking its model's alternatives.
    evaluated: dict[tuple[ForecastModel, str], tuple[str, ...] | None] = {}

    def check(model: ForecastModel, run: str) -> tuple[str, ...] | None:
        key = model, run
        if key not in evaluated:
            try:
                providers[model].check_run(run, requirement)
            except ForecastCoverageError as error:
                evaluated[key] = error.reason_codes
            else:
                evaluated[key] = None
        return evaluated[key]

    def latest(model: ForecastModel) -> tuple[str | None, tuple[str, ...]]:
        try:
            runs = providers[model].candidate_runs(requirement)
        except ForecastCoverageError as error:
            return None, error.reason_codes
        if not runs:
            # No source Run is not proof of model exclusion.
            raise RuntimeError(f"{model} discovery returned no Runs without coverage evidence")
        reasons: list[str] = []
        for run in runs:
            coverage = check(model, run)
            if coverage is None:
                return run, ()
            reasons.extend(coverage)
        return None, tuple(dict.fromkeys(reasons))

    def preferred() -> ForecastSelection:
        msm, reasons = latest("MSM")
        if msm is not None:
            return ForecastSelection("MSM", msm)
        gsm, gsm_reasons = latest("GSM")
        if gsm is not None:
            return ForecastSelection("GSM", gsm, fallback_from="MSM",
                                     coverage_reason_codes=reasons)
        raise ForecastUnavailable((*reasons, *gsm_reasons))

    if selected_run_id is None or refresh:
        return preferred()
    model = selected_model or "MSM"
    fixed_coverage = check(model, selected_run_id)
    if model == "MSM":
        msm, reasons = latest("MSM")
        if fixed_coverage is None:
            return ForecastSelection(model, selected_run_id,
                                     update_available=msm is not None and msm > selected_run_id)
        if msm is not None:
            return ForecastSelection(model, selected_run_id, update_available=True,
                                     update_required=True)
        gsm, gsm_reasons = latest("GSM")
        if gsm is not None:
            return ForecastSelection("GSM", gsm, fallback_from="MSM",
                                     coverage_reason_codes=reasons)
        raise ForecastUnavailable((*reasons, *gsm_reasons))

    # A pinned GSM never silently returns to MSM, even when it no longer covers.
    msm, msm_reasons = latest("MSM")
    if msm is not None:
        return ForecastSelection(model, selected_run_id, update_available=True,
                                 update_required=fixed_coverage is not None)
    gsm, gsm_reasons = latest("GSM")
    if fixed_coverage is not None and gsm is None:
        raise ForecastUnavailable((*msm_reasons, *gsm_reasons))
    return ForecastSelection(model, selected_run_id,
                             update_available=gsm is not None and gsm != selected_run_id,
                             update_required=fixed_coverage is not None)
