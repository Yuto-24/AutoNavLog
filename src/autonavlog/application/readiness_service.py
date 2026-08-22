from __future__ import annotations

from dataclasses import dataclass

from autonavlog.domain.calculation import CalculationOutcome, Issue
from autonavlog.domain.enums import IssueSeverity
from autonavlog.domain.planning import (
    PatternAltitudeValidationStatus,
    PersistedUiState,
    load_persisted_ui_state,
)
from autonavlog.domain.project import Project
from autonavlog.version import __version__

from .arrival import calculate_arrival_altitude
from .calculation_service import CalculationService
from .checkpoints import project_check_points
from .project_fingerprints import (
    current_calculation_input_fingerprint,
    defaults_review_fingerprint,
)
from .readiness import ReadinessEvaluation, evaluate_readiness


@dataclass(frozen=True)
class ReadinessFingerprints:
    calculation_input: str
    defaults_review: str


@dataclass(frozen=True)
class MaterializedReadiness:
    project: Project
    outcome: CalculationOutcome | None
    evaluation: ReadinessEvaluation
    fingerprints: ReadinessFingerprints


class ReadinessService:
    """Own fingerprint lifecycle and materialize one authoritative status."""

    def __init__(
        self,
        calculation_service: CalculationService,
        *,
        msm_package_version: str | None,
        require_crew_identification: bool = True,
        require_defaults_review: bool = True,
    ) -> None:
        self.calculation_service = calculation_service
        self.msm_package_version = msm_package_version
        self.require_crew_identification = require_crew_identification
        self.require_defaults_review = require_defaults_review

    @staticmethod
    def ui_state(project: Project) -> PersistedUiState | None:
        raw = project.metadata.get("ui_state")
        if raw is None:
            return None
        try:
            return load_persisted_ui_state(raw)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "PROJECT_STATE_INVALID: 保存済みUI状態を安全に読み込めません。"
            ) from error

    @staticmethod
    def _store_ui_state(project: Project, state: PersistedUiState) -> None:
        project.metadata["ui_state"] = state.model_dump(mode="json")

    def fingerprints(
        self,
        project: Project,
        outcome: CalculationOutcome | None,
        state: PersistedUiState,
    ) -> ReadinessFingerprints:
        selected = project.model_copy(deep=True)
        performance_version = (
            self.calculation_service.performance.manifest.source_revision
            if outcome is None
            else outcome.performance_table_version
        )
        calculation = current_calculation_input_fingerprint(
            selected,
            ui_state=state,
            performance=self.calculation_service.performance,
            calculation_policy_version=(
                self.calculation_service.policies.version
                if outcome is None
                else outcome.policy_version
            ),
            performance_table_version=performance_version,
            autonavlog_version=__version__,
            msm_package_version=self.msm_package_version,
        )
        defaults = defaults_review_fingerprint(
            selected,
            performance_table_version=performance_version,
            policy_version=(
                self.calculation_service.policies.version
                if outcome is None
                else outcome.policy_version
            ),
        )
        return ReadinessFingerprints(
            calculation_input=calculation,
            defaults_review=defaults,
        )

    @staticmethod
    def _departure_identity(
        project: Project,
        state: PersistedUiState,
    ) -> object:
        snapshot = state.reference_data_snapshot
        if snapshot is None:
            return project.departure_airport_id
        departure = snapshot.departure_airport
        return {
            "airport_id": departure.id,
            "latitude_deg": departure.latitude_deg,
            "longitude_deg": departure.longitude_deg,
        }

    def _validation_issues(
        self,
        project: Project,
        state: PersistedUiState | None,
        *,
        editable: bool,
    ) -> tuple[list[Issue], list[Issue]]:
        reference: list[Issue] = []
        project_issues: list[Issue] = []
        if self.require_crew_identification and not project.pilot_name.strip():
            project_issues.append(
                Issue(
                    code="PILOT_REQUIRED",
                    severity=IssueSeverity.BLOCKER,
                    message="NAV2へ転記するPILOTを入力してください。",
                )
            )
        if self.require_crew_identification and not project.ship_identifier.strip():
            project_issues.append(
                Issue(
                    code="SHIP_REQUIRED",
                    severity=IssueSeverity.BLOCKER,
                    message="NAV2へ転記するSHIPを入力してください。",
                )
            )
        performance = self.calculation_service.performance
        performance_problems = performance.readiness_problems(project.aircraft_profile_id)
        validation_reason = performance.validation_issue_reason()
        if performance_problems and validation_reason != "HASH_MISMATCH":
            reference.append(
                Issue(
                    code="PERFORMANCE_DATA_UNAVAILABLE",
                    severity=IssueSeverity.BLOCKER,
                    message=(
                        "選択機体に必要な性能データを解決できません: "
                        + " / ".join(performance_problems)
                    ),
                    metadata={
                        "reason": "RUNTIME_READINESS",
                        "aircraft_profile_id": project.aircraft_profile_id,
                        "details": list(performance_problems),
                    },
                )
            )
        manifest = performance.manifest
        if validation_reason is not None:
            reference.append(
                Issue(
                    code="PERFORMANCE_DATA_UNVERIFIED",
                    severity=IssueSeverity.BLOCKER,
                    message="検証済み性能データを選択してください。",
                    metadata={
                        "reason": validation_reason,
                        "source_revision": manifest.source_revision,
                        "validation_status": manifest.validation_status,
                    },
                )
            )
        if state is None:
            return reference, project_issues
        snapshot = state.reference_data_snapshot
        if snapshot is None:
            reference.append(
                Issue(
                    code="AIRPORT_DATA_UNAVAILABLE",
                    severity=IssueSeverity.BLOCKER,
                    message="FROM/TOをactive参照データから選択してください。",
                )
            )
        else:
            if (
                snapshot.departure_airport.id != project.departure_airport_id
                or snapshot.destination_airport.id != project.destination_airport_id
            ):
                project_issues.append(
                    Issue(
                        code="PROJECT_STATE_INVALID",
                        severity=IssueSeverity.BLOCKER,
                        message="FROM/TOと保存済み参照snapshotを再確認してください。",
                    )
                )
            if (
                snapshot.destination_airport.pattern_altitude_validation_status
                != PatternAltitudeValidationStatus.VERIFIED
            ):
                reference.append(
                    Issue(
                        code="PATTERN_ALTITUDE_REQUIRED",
                        severity=IssueSeverity.BLOCKER,
                        message=("目的空港の実運用場周経路高度を一次資料で検証してください。"),
                        metadata={
                            "destination_airport_id": (snapshot.destination_airport.id),
                            "validation_status": (
                                snapshot.destination_airport.pattern_altitude_validation_status.value
                            ),
                        },
                    )
                )
        arrival = calculate_arrival_altitude(project, state)
        for issue in arrival.issues:
            if issue.code in {
                "AIRPORT_DATA_UNAVAILABLE",
                "PATTERN_ALTITUDE_REQUIRED",
            }:
                reference.append(issue)
            else:
                project_issues.append(issue)
        if project.metadata.get("ui_state_reconfirmation_required") is True:
            project_issues.append(
                Issue(
                    code="PROJECT_STATE_INVALID",
                    severity=IssueSeverity.BLOCKER,
                    message="旧ProjectのFROM/TO・VREP・参照行を再確認してください。",
                )
            )
        project_issues.extend(project_check_points(project).issues)
        if not project.route_nodes or not project.sections:
            project_issues.append(
                Issue(
                    code="ROUTE_INCOMPLETE",
                    severity=IssueSeverity.BLOCKER,
                    message="RouteとSectionを確定してください。",
                )
            )
        return reference, project_issues

    def evaluate(
        self,
        project: Project,
        outcome: CalculationOutcome | None,
        *,
        editable: bool = True,
    ) -> MaterializedReadiness:
        state = self.ui_state(project)
        reference_issues, project_issues = self._validation_issues(
            project,
            state,
            editable=editable,
        )
        if state is None:
            fingerprints = ReadinessFingerprints("", "")
            current_calculation = None
            current_defaults = None
        else:
            fingerprints = self.fingerprints(project, outcome, state)
            current_calculation = fingerprints.calculation_input
            current_defaults = (
                fingerprints.defaults_review if self.require_defaults_review else None
            )
        evaluation = evaluate_readiness(
            project,
            outcome,
            ui_state=state,
            current_calculation_input_fingerprint=current_calculation,
            current_defaults_review_fingerprint=current_defaults,
            reference_data_issues=reference_issues,
            project_validation_issues=project_issues,
            editable=editable,
        )
        materialized_project = project.model_copy(
            deep=True,
            update={"status": evaluation.status},
        )
        materialized_outcome = (
            None
            if outcome is None
            else outcome.model_copy(
                deep=True,
                update={"status": evaluation.status},
            )
        )
        return MaterializedReadiness(
            project=materialized_project,
            outcome=materialized_outcome,
            evaluation=evaluation,
            fingerprints=fingerprints,
        )

    def record_calculation(
        self,
        project: Project,
        outcome: CalculationOutcome,
    ) -> MaterializedReadiness:
        state = self.ui_state(project)
        if state is None:
            state = PersistedUiState()
        prepared = project.model_copy(
            deep=True,
            update={
                "selected_forecast_run_id": outcome.selected_forecast_run_id,
            },
        )
        fingerprints = self.fingerprints(prepared, outcome, state)
        state = state.model_copy(
            update={"calculated_against_fingerprint": (fingerprints.calculation_input)}
        )
        self._store_ui_state(prepared, state)
        return self.evaluate(prepared, outcome)

    def confirm_defaults(self, project: Project, outcome: CalculationOutcome | None) -> None:
        state = self.ui_state(project)
        if state is None:
            raise ValueError("参照データとVREPを先に確認してください。")
        fingerprints = self.fingerprints(project, outcome, state)
        self._store_ui_state(
            project,
            state.model_copy(update={"defaults_review_fingerprint": fingerprints.defaults_review}),
        )
