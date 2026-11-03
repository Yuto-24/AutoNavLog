"""Issue #117: existing application workflow in a transient Pyodide filesystem.

No HTTP server or alternative calculation core. Browser adapters own network acquisition.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ValidationError

from autonavlog.application.project_fingerprints import forecast_selection_fingerprint
from autonavlog.application.project_service import ProjectService
from autonavlog.domain.calculation import Issue
from autonavlog.domain.enums import IssueSeverity
from autonavlog.forecast_backup import (
    WEATHER_FAILURES,
    BackupInput,
    BackupRequest,
    BackupResult,
    calculation_contract,
    calculation_project,
    input_identity,
)
from autonavlog.forecast_backup_messages import MESSAGES
from autonavlog.importers.kml import (
    KmlDocumentSelectionRequired,
    KmlImportError,
    import_kml_or_kmz,
    import_kml_text,
)
from autonavlog.local_persistence import migrate_record
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.storage.reference_data import ReferenceDataCatalogRepository
from autonavlog.storage.rjfm_inbound_reference import RjfmInboundGuidanceReference
from autonavlog.storage.rjfm_reference import RjfmReferencePack
from autonavlog.weather.complete_forecast import CompleteForecastProvider
from autonavlog.weather.destination_taf import DecodedTafProvider
from autonavlog.weather.local_msm import LocalMsmWeather, LocalWeatherError
from autonavlog.weather.msm_adapter import MsmWeatherProvider
from autonavlog.weather.msm_fixture import FIXTURE_WEATHER_LABEL, fixture_weather_provider
from autonavlog.web.facade import AutoNavLogWebApplication, WebApplicationError
from autonavlog.web.models import (
    AcknowledgeRequest,
    CalculationRequest,
    ConfirmRouteRequest,
    ImportRouteRequest,
    RenameRouteNodeRequest,
    ReplaceCheckPointsRequest,
    UpdateProjectRequest,
    WorkingRecovery,
)


class LocalRenameRouteNodeRequest(RenameRouteNodeRequest):
    node_id: UUID


class _RequestValidationError(ValueError):
    def __init__(self, error: ValidationError) -> None:
        super().__init__("入力内容を確認してください。")
        self.issues = [
            {"location": list(issue["loc"]), "message": issue["msg"], "type": issue["type"]}
            for issue in error.errors(include_url=False, include_context=False, include_input=False)
        ]


def _validate_request[T: BaseModel](model: type[T], payload: dict[str, Any]) -> T:
    # Only request-model validation is a user input failure. Execution errors are not.
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise _RequestValidationError(error) from error


class _LocalFacade(AutoNavLogWebApplication):
    @staticmethod
    def _assert_project_owner(project: Any, owner_id: str) -> None:
        # Local-only data has no authentication/owner boundary.
        pass


class LocalApplication:
    def __init__(self, data_root: Path, *, forecast_fixture: Path | None = None) -> None:
        self._data_root = data_root
        self._forecast_fixture = forecast_fixture
        self.local_weather = None if forecast_fixture else LocalMsmWeather()
        local_weather = self.local_weather

        def weather_factory() -> MsmWeatherProvider:
            if forecast_fixture:
                return fixture_weather_provider(forecast_fixture)
            assert local_weather is not None
            return local_weather.provider

        # Reuse existing repository behavior on MEMFS; never mount IDBFS/OPFS.
        self._temporary = TemporaryDirectory(prefix="autonavlog-local-")
        storage = Path(self._temporary.name)
        references = ReferenceDataCatalogRepository(
            storage / "reference", bundled_default=data_root / "reference/default"
        )
        catalog = references.open_active()
        self.app = _LocalFacade(
            project_service=ProjectService(LocalProjectRepository(storage)),
            airports=AirportRepository.from_reference_catalog(catalog),
            performance=PerformanceRepository.from_directory_for_application(
                data_root / "performance"
            ),
            rjfm_reference_pack=RjfmReferencePack.from_directory(data_root / "reference/rjfm"),
            rjfm_inbound_guidance_reference=RjfmInboundGuidanceReference.from_directory(
                data_root / "reference/rjfm-inbound-guidance"
            ),
            reference_repository=references,
            reference_catalog=catalog,
            weather_factory=weather_factory,
            weather_label=FIXTURE_WEATHER_LABEL if forecast_fixture else "実MSM（端末内処理）",
            development_weather=False,
            destination_wind_provider=DecodedTafProvider([], "TAF_PROXY_NOT_CONFIGURED"),
        )
        self.session = self.app.create_session(
            "local-poc", restore_persisted=False, persist_working=False
        )
        self.session.weather_provider = CompleteForecastProvider(self.session.weather_provider)
        self._backup_context: tuple[BackupInput, bool] | None = None

    def validate_calculation_input(self, payload: dict[str, Any]) -> None:
        request = _validate_request(CalculationRequest, payload)
        if self.session.project is None:
            raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
        project = self.session.project.model_copy(deep=True)
        self.app._normalize_rjfm_departure(project)
        plan = self.app.project_service.ui_state(project).arrival_plan
        if (
            plan is None
            or plan.selected_pattern_altitude_ft_msl is None
            or plan.selected_pattern_altitude_source is None
        ):
            raise WebApplicationError(
                "PATTERN_ALTITUDE_REQUIRED",
                "目的空港の参照データ上の場周経路高度と、今回採用する高度を確認してください。",
                status_code=409,
            )
        outcome = self.session.calculation_service.calculate(
            project,
            self.session.weather_provider,
            validate_only=True,
            rounding_mode=self.session.rounding_mode
            if request.rounding_mode is None
            else request.rounding_mode,
        )
        if outcome.blockers:
            raise WebApplicationError(
                "VALIDATION_FAILED", " / ".join(item.message for item in outcome.blockers[:3])
            )

    def prepare_forecast_backup(self, payload: dict[str, Any]) -> BackupInput:
        self.validate_calculation_input({"rounding_mode": payload.get("rounding_mode")})
        request = _validate_request(
            CalculationRequest,
            {
                "rounding_mode": payload.get("rounding_mode"),
            },
        )
        if self.session.project is None or self.session.project.weather_mode != "FORECAST":
            raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
        project = self.session.project.model_copy(deep=True)
        self.app._normalize_rjfm_departure(project)
        rounding = (
            self.session.rounding_mode if request.rounding_mode is None else request.rounding_mode
        )
        refresh = self.app.forecast_refresh_requested(self.session, project, rounding_mode=rounding)
        prepared = BackupInput(
            contract=calculation_contract(self._data_root),
            project=calculation_project(project),
            rounding_mode=rounding,
            refresh_forecast=refresh,
            input_identity=input_identity(project, rounding, refresh),
            reason=payload["reason"],
        )
        self._backup_context = (prepared, self.session.rounding_mode)
        return prepared

    def adopt_forecast_backup(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = _validate_request(BackupRequest, payload["request"])
        try:
            result = BackupResult.model_validate(payload["result"])
        except ValidationError as error:
            raise WebApplicationError(
                "FORECAST_BACKUP_INVALID", MESSAGES["invalid_result"]
            ) from error
        if self._backup_context is None or self.session.project is None:
            raise WebApplicationError("FORECAST_BACKUP_STALE", MESSAGES["stale_result"])
        current, starting_mode = self._backup_context
        live_project = self.session.project.model_copy(deep=True)
        self.app._normalize_rjfm_departure(live_project)
        if (
            self.session.rounding_mode != starting_mode
            or request.rounding_mode != current.rounding_mode
            or input_identity(live_project, request.rounding_mode, request.refresh_forecast)
            != current.input_identity
        ):
            raise WebApplicationError("FORECAST_BACKUP_STALE", MESSAGES["stale_result"])
        if request.contract != current.contract or result.contract != current.contract:
            raise WebApplicationError("FORECAST_BACKUP_INCOMPATIBLE", MESSAGES["incompatible"])
        if (
            request.input_identity != current.input_identity
            or result.input_identity != request.input_identity
            or result.request_id != request.request_id
            or result.rounding_mode != request.rounding_mode
        ):
            raise WebApplicationError("FORECAST_BACKUP_STALE", MESSAGES["stale_result"])
        last = result.calculation.model_copy(deep=True)
        if (
            last.project.id != current.project.id
            or last.project.revision != current.project.revision
            or forecast_selection_fingerprint(last.project, rounding_mode=request.rounding_mode)
            != forecast_selection_fingerprint(current.project, rounding_mode=request.rounding_mode)
            or last.outcome.blockers
            or last.outcome.selected_forecast_run_id is None
            or last.forecast_metadata.get("forecast_run_id")
            != last.outcome.selected_forecast_run_id
            or (
                not request.refresh_forecast
                and current.project.selected_forecast_run_id is not None
                and last.outcome.selected_forecast_run_id
                != current.project.selected_forecast_run_id
            )
        ):
            raise WebApplicationError("FORECAST_BACKUP_INVALID", MESSAGES["invalid_result"])
        assert self.session.project is not None
        original = self.session.project
        last.project.name = original.name
        last.project.pilot_name = original.pilot_name
        last.project.ship_identifier = original.ship_identifier
        last.project.metadata = {**original.metadata, "ui_state": last.project.metadata["ui_state"]}
        materialized = self.session.readiness_service.evaluate(last.project, last.outcome)
        if (
            not materialized.evaluation.calculation_is_current
            or materialized.fingerprints.calculation_input != last.calculation_fingerprint
        ):
            raise WebApplicationError("FORECAST_BACKUP_INVALID", MESSAGES["invalid_result"])
        provenance = {
            "source": "forecast_backup_server",
            "reason": request.reason,
            "request_id": str(request.request_id),
            "selected_run": last.outcome.selected_forecast_run_id,
            "contract": result.contract.model_dump(mode="json"),
        }
        last.forecast_metadata["calculation_source"] = provenance
        last.outcome.issues.append(
            Issue(
                code="FORECAST_BACKUP_USED",
                severity=IssueSeverity.WARNING,
                message=MESSAGES["backup_success"],
                metadata=provenance,
            )
        )
        self._backup_context = None
        self.session.project = last.project.model_copy(deep=True)
        self.session.outcome = last.outcome.model_copy(deep=True)
        self.session.last_calculation = last
        self.session.destination_wind = last.destination_wind
        self.session.rounding_mode = request.rounding_mode
        self.session.readiness = None
        self.session.forecast_update_fingerprint = (
            forecast_selection_fingerprint(last.project, rounding_mode=request.rounding_mode)
            if result.refresh_available
            else None
        )
        return self.app.present(self.session)

    def new_work(self) -> str:
        """Replace all mutable work while keeping the interpreter and installed packages."""
        replacement = LocalApplication(self._data_root, forecast_fixture=self._forecast_fixture)
        try:
            state = replacement.dispatch("state")
        except Exception:
            replacement.close()
            raise
        self._temporary.cleanup()
        self._temporary = replacement._temporary
        self.local_weather = replacement.local_weather
        self.app = replacement.app
        self.session = replacement.session
        self._backup_context = None
        return state

    def requires_weather(self) -> bool:
        return bool(
            self.local_weather
            and self.session.project
            and self.session.project.weather_mode == "FORECAST"
        )

    def weather_plan(
        self,
        catalog_json: str,
        calculation: dict[str, Any] | None = None,
    ) -> str:
        """Adapter preflight, called by the Worker before synchronous calculation."""
        assert self.local_weather is not None and self.session.project is not None
        try:
            rounding_mode = _validate_request(CalculationRequest, calculation or {}).rounding_mode
            project = self.session.project.model_copy(deep=True)
            self.app._normalize_rjfm_departure(project)
            requirement = (
                self.session.calculation_service.forecast_service.build_initial_requirement(project)
            )
            return self.local_weather.plan(
                project,
                requirement,
                catalog_json,
                refresh_forecast=self.app.forecast_refresh_requested(
                    self.session, project, rounding_mode=rounding_mode
                ),
            )
        except LocalWeatherError as error:
            return json.dumps({"error": {"code": error.code, "message": str(error)}})

    def accept_weather(self, payload: bytes, sha256: str) -> str:
        assert self.local_weather is not None
        try:
            self.local_weather.accept(payload, sha256)
            return "{}"
        except LocalWeatherError as error:
            return json.dumps({"error": {"code": error.code, "message": str(error)}})

    def destination_taf_airport(self, operation: str, payload: dict[str, Any]) -> str | None:
        """Prepare acquisition outside the calculation core and invalidate previous records."""
        if operation not in {"calculate", "updateAndRecalculate"}:
            return None
        self.app.destination_wind_provider = DecodedTafProvider([], "TAF_PROXY_NOT_CONFIGURED")
        project = self.session.project
        mode = payload.get("weather_mode", project.weather_mode if project else None)
        if project is None or mode != "FORECAST":
            return None
        return self.app.airports.get(project.destination_airport_id).icao

    def set_destination_taf(self, acquisition: dict[str, Any]) -> None:
        self.app.destination_wind_provider = DecodedTafProvider(
            acquisition["records"], acquisition["reason_code"]
        )

    def dispatch(self, path: str, body: dict[str, Any] | None = None) -> str:
        """Execute application operations using the existing facade on transient MEMFS."""
        payload = body or {}
        if path in {
            "newWork",
            "bootstrap",
            "refreshSavedWork",
            "adoptSavedProject",
            "importRoute",
            "confirmRoute",
            "updateProject",
            "updateAndRecalculate",
            "calculate",
            "replaceCheckPoints",
            "renameRouteNode",
            "acknowledge",
        }:
            self._backup_context = None
        if path == "validateCalculation":
            self.validate_calculation_input(payload)
            return "{}"
        if path == "prepareForecastBackup":
            return self.prepare_forecast_backup(payload).model_dump_json()
        if path == "adoptForecastBackup":
            return json.dumps(
                self.adopt_forecast_backup(payload), ensure_ascii=False, allow_nan=False
            )
        if path == "newWork":
            return self.new_work()
        if path == "prepareSyncResolution":
            record = migrate_record(payload["record"])
            last = record.lastCalculation
            if last is not None:
                ui = self.session.readiness_service.ui_state(record.draft)
                fingerprint = (
                    self.session.readiness_service.fingerprints(
                        record.draft, last.outcome, ui
                    ).calculation_input
                    if ui is not None
                    else None
                )
                if fingerprint != last.calculation_fingerprint:
                    record.lastCalculation = None
            if payload.get("copy_id"):
                identity = UUID(payload["copy_id"])
                record.id = identity
                record.draft.id = identity
                record.draft.revision = 0
                if record.checkpoint is not None:
                    record.checkpoint.id = identity
                    record.checkpoint.revision = 0
                if record.lastCalculation is not None:
                    record.lastCalculation.project.id = identity
                    record.lastCalculation.project.revision = 0
                    record.lastCalculation.outcome.project_id = identity
            return migrate_record(record.model_dump(mode="json")).model_dump_json()
        if path == "validateRecord":
            return migrate_record(payload).model_dump_json()
        if path == "state":
            state = self.app.present(self.session)
        elif path in {"bootstrap", "refreshSavedWork", "adoptSavedProject"}:
            recovery_payload = payload if path == "bootstrap" else payload["working"]
            refresh_mode = _validate_request(
                CalculationRequest, {"rounding_mode": payload.get("rounding_mode")}
            ).rounding_mode
            pending_refresh = (
                self.session.forecast_update_fingerprint if path == "adoptSavedProject" else None
            )
            state = (
                self.app.restore_working(
                    self.session,
                    _validate_request(WorkingRecovery, recovery_payload),
                    reset_rounding=path == "bootstrap",
                    rounding_mode=bool(refresh_mode),
                )
                if recovery_payload
                else self.app.present(self.session)
            )
            # Explicit Save adopts name/revision changes in the same working
            # session. Reopen/reload use bootstrap and never inherit intent.
            if (
                pending_refresh is not None
                and self.session.project is not None
                and pending_refresh
                == forecast_selection_fingerprint(
                    self.session.project, rounding_mode=self.session.rounding_mode
                )
            ):
                self.session.forecast_update_fingerprint = pending_refresh
        elif path == "importRoute":
            request = _validate_request(ImportRouteRequest, payload)
            if request.kml_text is not None:
                content = request.kml_text.encode("utf-8")
            else:
                encoded = request.content_base64 or ""
                if len(encoded) > 14 * 1024 * 1024:
                    raise WebApplicationError(
                        "UPLOAD_TOO_LARGE", "KML/KMZは10 MiB以下にしてください。"
                    )
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise WebApplicationError(
                        "UPLOAD_ENCODING_INVALID", "アップロード内容を読み取れません。"
                    ) from error
            if len(content) > 10 * 1024 * 1024:
                raise WebApplicationError("UPLOAD_TOO_LARGE", "KML/KMZは10 MiB以下にしてください。")
            result = (
                import_kml_text(request.kml_text, filename=request.filename)
                if request.kml_text is not None
                else import_kml_or_kmz(
                    content, filename=request.filename, kmz_kml_filename=request.kmz_kml_filename
                )
            )
            state = self.app.accept_import(self.session, result=result, filename=request.filename)
        elif path in {"confirmRoute", "updateProject", "updateAndRecalculate"}:
            if path == "confirmRoute":
                state = self.app.confirm_route(
                    self.session, _validate_request(ConfirmRouteRequest, payload)
                )
            else:
                update = _validate_request(UpdateProjectRequest, payload)
                state = (
                    self.app.update_and_calculate(self.session, update)
                    if path == "updateAndRecalculate"
                    else self.app.update_project(self.session, update)
                )
        elif path == "calculate":
            if self.session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            calculation_request = _validate_request(CalculationRequest, payload)
            fields = (
                "project",
                "outcome",
                "last_calculation",
                "destination_wind",
                "readiness",
                "rounding_mode",
                "forecast_update_fingerprint",
            )
            before = {field: getattr(self.session, field) for field in fields}
            provider = self.session.weather_provider
            if isinstance(provider, CompleteForecastProvider):
                provider.failure_code = None
            try:
                state = self.app.calculate(
                    self.session, rounding_mode=calculation_request.rounding_mode
                )
                reason = getattr(provider, "failure_code", None)
                if reason in WEATHER_FAILURES:
                    raise LocalWeatherError(reason, MESSAGES["backup_progress"])
            except Exception as error:
                for field, value in before.items():
                    setattr(self.session, field, value)
                if isinstance(error, LocalWeatherError) and error.code in WEATHER_FAILURES:
                    raise WebApplicationError(error.code, MESSAGES["backup_progress"]) from error
                raise
        elif path == "replaceCheckPoints":
            state = self.app.replace_check_points(
                self.session, _validate_request(ReplaceCheckPointsRequest, payload)
            )
        elif path == "renameRouteNode":
            request_name = _validate_request(LocalRenameRouteNodeRequest, payload)
            state = self.app.rename_route_node(
                self.session, request_name.node_id, request_name.name
            )
        elif path == "acknowledge":
            acknowledgement = _validate_request(
                AcknowledgeRequest, {"checked": payload.get("checked")}
            )
            state = self.app.acknowledge(
                self.session,
                payload["key"],
                acknowledgement.checked,
            )
        else:
            raise WebApplicationError("LOCAL_UNSUPPORTED", "この操作はLocal PoCの対象外です。")
        # The reused facade's owner metadata is a Legacy detail, never Local data.
        for project in [
            self.session.project,
            self.session.last_calculation.project if self.session.last_calculation else None,
        ]:
            if project is not None:
                project.metadata.pop("web_owner_id", None)
        for project in [
            state.get("project"),
            state["workingRecovery"].get("project"),
            (state["workingRecovery"].get("last_calculation") or {}).get("project"),
        ]:
            if project is not None:
                project["metadata"].pop("web_owner_id", None)
        # Durable summaries are supplied by the browser Repository.
        state["savedProjects"] = []
        return json.dumps(state, ensure_ascii=False, allow_nan=False)

    def dispatch_response(
        self,
        operation: str,
        body: dict[str, Any] | None = None,
        *,
        error_operation: str | None = None,
    ) -> str:
        """Serialize errors explicitly: Comlink/Python exceptions lose custom fields."""
        details: dict[str, Any] = {}
        try:
            return self.dispatch(operation, body)
        except _RequestValidationError as error:
            code, message = "VALIDATION_FAILED", str(error)
            details["issues"] = error.issues
        except WebApplicationError as error:
            code, message = error.code, str(error)
        except KmlDocumentSelectionRequired as error:
            code, message = (
                "KMZ_DOCUMENT_SELECTION_REQUIRED",
                "KMZ内で使用するKMLを選択してください。",
            )
            details["candidates"] = list(error.candidates)
        except KmlImportError as error:
            code, message = "KML_IMPORT_FAILED", str(error)
        except Exception:
            public_operation = error_operation or operation
            code = "CALCULATION_JOB_FAILED" if public_operation == "calculate" else "REQUEST_FAILED"
            message = (
                "計算に失敗しました。"
                if public_operation == "calculate"
                else "処理に失敗しました。"
            )
        return json.dumps(
            {"error": {"code": code, "message": message, "details": details}},
            ensure_ascii=False,
            allow_nan=False,
        )

    def close(self) -> None:
        self._temporary.cleanup()
