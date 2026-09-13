"""Issue #117: existing application workflow in a transient Pyodide filesystem.

No browser persistence, HTTP server, forecast acquisition, or alternative calculation core.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import unquote
from uuid import UUID

from autonavlog.application.project_service import ProjectService
from autonavlog.importers.kml import import_kml_or_kmz, import_kml_text
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
        """Retain current UI operation names until #118; these are not HTTP requests."""
        payload = body or {}
        if path == "/api/state":
            state = self.app.present(self.session)
        elif path == "/api/import":
            request = ImportRouteRequest.model_validate(payload)
            if not request.filename.lower().endswith(".kml") or request.kmz_kml_filename:
                raise ValueError("Local PoCはKMLのみ対応しています。KMZは未対応です。")
            if request.kml_text is not None:
                content = request.kml_text.encode("utf-8")
            else:
                encoded = request.content_base64 or ""
                if len(encoded) > 4 * ((10 * 1024 * 1024 + 2) // 3):
                    raise ValueError("KMLは10 MiB以下にしてください。")
                content = base64.b64decode(encoded, validate=True)
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("KMLは10 MiB以下にしてください。")
            if content.startswith(b"PK"):
                raise ValueError("Local PoCはKMZに対応していません。")
            result = (
                import_kml_text(request.kml_text, filename=request.filename)
                if request.kml_text is not None
                else import_kml_or_kmz(content, filename=request.filename)
            )
            state = self.app.accept_import(self.session, result=result, filename=request.filename)
        elif path in {"/api/route/confirm", "/api/project", "/api/project/recalculate"}:
            if path == "/api/route/confirm":
                state = self.app.confirm_route(
                    self.session, ConfirmRouteRequest.model_validate(payload)
                )
            else:
                update = UpdateProjectRequest.model_validate(payload)
                state = (
                    self.app.update_and_calculate(self.session, update)
                    if path.endswith("/recalculate")
                    else self.app.update_project(self.session, update)
                )
        elif path == "/api/calculate":
            if self.session.project is None:
                raise ValueError("先に経路を確定してください。")
            state = self.app.calculate(self.session)
        elif path == "/api/project/check-points":
            state = self.app.replace_check_points(
                self.session, ReplaceCheckPointsRequest.model_validate(payload)
            )
        elif path.startswith("/api/project/route-nodes/") and path.endswith("/name"):
            request_name = RenameRouteNodeRequest.model_validate(payload)
            state = self.app.rename_route_node(
                self.session, UUID(path.split("/")[-2]), request_name.name
            )
        elif path.startswith("/api/acknowledgements/"):
            acknowledgement = AcknowledgeRequest.model_validate(payload)
            state = self.app.acknowledge(
                self.session,
                unquote(path.removeprefix("/api/acknowledgements/")),
                acknowledgement.checked,
            )
        else:
            raise WebApplicationError("LOCAL_UNSUPPORTED", "この操作はLocal PoCの対象外です。")
        # The existing facade's temporary autosave must not advertise durable storage.
        state["savedProjects"] = []
        return json.dumps(state, ensure_ascii=False, allow_nan=False)

    def close(self) -> None:
        self._temporary.cleanup()
