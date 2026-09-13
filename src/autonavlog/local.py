"""Issue #117: existing application workflow in a transient Pyodide filesystem.

No browser persistence, HTTP server, forecast acquisition, or alternative calculation core.
"""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from autonavlog.application.project_service import ProjectService
from autonavlog.importers.kml import KmlImportError, import_kml_or_kmz, import_kml_text
from autonavlog.performance.repository import PerformanceRepository
from autonavlog.storage.airports import AirportRepository
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.storage.reference_data import ReferenceDataCatalogRepository
from autonavlog.storage.rjfm_inbound_reference import RjfmInboundGuidanceReference
from autonavlog.storage.rjfm_reference import RjfmReferencePack
from autonavlog.weather.msm_fixture import FIXTURE_WEATHER_LABEL, fixture_weather_provider
from autonavlog.web.facade import AutoNavLogWebApplication, WebApplicationError
from autonavlog.web.models import (
    AcknowledgeRequest,
    ConfirmRouteRequest,
    ImportRouteRequest,
    RenameRouteNodeRequest,
    ReplaceCheckPointsRequest,
    UpdateProjectRequest,
)


class LocalRenameRouteNodeRequest(RenameRouteNodeRequest):
    node_id: UUID


class LocalApplication:
    def __init__(self, data_root: Path, *, forecast_fixture: Path | None = None) -> None:
        # Reuse existing repository behavior on MEMFS; never mount IDBFS/OPFS.
        self._temporary = TemporaryDirectory(prefix="autonavlog-local-")
        storage = Path(self._temporary.name)
        references = ReferenceDataCatalogRepository(
            storage / "reference", bundled_default=data_root / "reference/default"
        )
        catalog = references.open_active()
        self.app = AutoNavLogWebApplication(
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
            weather_factory=lambda: fixture_weather_provider(
                forecast_fixture or data_root / "msm-fixture"
            ),
            weather_label=FIXTURE_WEATHER_LABEL,
            development_weather=False,
        )
        self.session = self.app.create_session("local-poc")

    def dispatch(self, path: str, body: dict[str, Any] | None = None) -> str:
        """Execute application operations using the existing facade on transient MEMFS."""
        payload = body or {}
        if path == "bootstrap":
            state = self.app.present(self.session)
        elif path == "importRoute":
            request = ImportRouteRequest.model_validate(payload)
            if not request.filename.lower().endswith(".kml") or request.kmz_kml_filename:
                raise WebApplicationError(
                    "LOCAL_UNSUPPORTED", "Local PoCはKMLのみ対応しています。KMZは未対応です。"
                )
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
            if content.startswith(b"PK"):
                raise WebApplicationError("LOCAL_UNSUPPORTED", "Local PoCはKMZに対応していません。")
            result = (
                import_kml_text(request.kml_text, filename=request.filename)
                if request.kml_text is not None
                else import_kml_or_kmz(content, filename=request.filename)
            )
            state = self.app.accept_import(self.session, result=result, filename=request.filename)
        elif path in {"confirmRoute", "updateProject", "updateAndRecalculate"}:
            if path == "confirmRoute":
                state = self.app.confirm_route(
                    self.session, ConfirmRouteRequest.model_validate(payload)
                )
            else:
                update = UpdateProjectRequest.model_validate(payload)
                state = (
                    self.app.update_and_calculate(self.session, update)
                    if path == "updateAndRecalculate"
                    else self.app.update_project(self.session, update)
                )
        elif path == "calculate":
            if self.session.project is None:
                raise WebApplicationError("PROJECT_REQUIRED", "先に経路を確定してください。")
            state = self.app.calculate(self.session)
        elif path == "replaceCheckPoints":
            state = self.app.replace_check_points(
                self.session, ReplaceCheckPointsRequest.model_validate(payload)
            )
        elif path == "renameRouteNode":
            request_name = LocalRenameRouteNodeRequest.model_validate(payload)
            state = self.app.rename_route_node(
                self.session, request_name.node_id, request_name.name
            )
        elif path == "acknowledge":
            acknowledgement = AcknowledgeRequest.model_validate({"checked": payload.get("checked")})
            state = self.app.acknowledge(
                self.session,
                payload["key"],
                acknowledgement.checked,
            )
        else:
            raise WebApplicationError("LOCAL_UNSUPPORTED", "この操作はLocal PoCの対象外です。")
        # The existing facade's temporary autosave must not advertise durable storage.
        state["savedProjects"] = []
        return json.dumps(state, ensure_ascii=False, allow_nan=False)

    def dispatch_response(self, operation: str, body: dict[str, Any] | None = None) -> str:
        """Serialize errors explicitly: Comlink/Python exceptions lose custom fields."""
        details: dict[str, Any] = {}
        try:
            return self.dispatch(operation, body)
        except ValidationError as error:
            code, message = "VALIDATION_FAILED", "入力内容を確認してください。"
            details["issues"] = [
                {"location": list(issue["loc"]), "message": issue["msg"], "type": issue["type"]}
                for issue in error.errors(
                    include_url=False, include_context=False, include_input=False
                )
            ]
        except WebApplicationError as error:
            code, message = error.code, str(error)
        except KmlImportError as error:
            code, message = "KML_IMPORT_FAILED", str(error)
        except Exception as error:
            code = "CALCULATION_JOB_FAILED" if operation == "calculate" else "REQUEST_FAILED"
            message = str(error)
        return json.dumps(
            {"error": {"code": code, "message": message, "details": details}},
            ensure_ascii=False,
            allow_nan=False,
        )

    def close(self) -> None:
        self._temporary.cleanup()
