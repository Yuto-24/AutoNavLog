from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from autonavlog.importers.kml import (
    KmlDocumentSelectionRequired,
    KmlImportError,
    import_kml_or_kmz,
    import_kml_text,
)
from autonavlog.version import __version__

from .facade import AutoNavLogWebApplication, WebApplicationError, WebSession
from .models import (
    AcknowledgeRequest,
    ConfirmRouteRequest,
    ImportRouteRequest,
    LoadProjectRequest,
    SaveProjectRequest,
    UpdateProjectRequest,
)
from .runtime import WeatherMode, WebRuntimeConfig, build_web_application

MAX_BASE64_CHARACTERS = 14 * 1024 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SESSION_COOKIE_NAME = "autonavlog_session"
CLOUDFLARE_IDENTITY_HEADER = "Cf-Access-Authenticated-User-Email"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://*.tile.openstreetmap.org; "
            "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response


def _owner_identity(request: Request) -> str:
    web = cast(AutoNavLogWebApplication, request.app.state.web_application)
    raw_identity = (
        web.trusted_local_identity or request.headers.get(CLOUDFLARE_IDENTITY_HEADER) or ""
    )
    identity = raw_identity.strip().casefold()
    if not identity:
        raise WebApplicationError(
            "AUTHENTICATION_REQUIRED",
            "Cloudflare Accessで認証してからアクセスしてください。",
            status_code=401,
        )
    if len(identity) > 320 or any(ord(character) < 32 for character in identity):
        raise WebApplicationError(
            "AUTHENTICATION_INVALID",
            "認証ユーザー情報を検証できません。",
            status_code=401,
        )
    return identity


def require_session(request: Request) -> WebSession:
    session_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_token:
        raise WebApplicationError(
            "SESSION_REQUIRED",
            "画面を再読み込みしてセッションを作成してください。",
            status_code=401,
        )
    web = cast(AutoNavLogWebApplication, request.app.state.web_application)
    return web.session(session_token, _owner_identity(request))


SessionDependency = Annotated[WebSession, Depends(require_session)]


def _environment_config() -> WebRuntimeConfig:
    raw_weather_mode = os.environ.get("AUTONAVLOG_WEATHER", "fake")
    if raw_weather_mode not in {"fake", "msm", "msm-metar"}:
        raise RuntimeError("AUTONAVLOG_WEATHER must be fake, msm, or msm-metar")

    return WebRuntimeConfig(
        data_root=Path(os.environ.get("AUTONAVLOG_DATA_ROOT", "data")),
        storage_root=Path(os.environ.get("AUTONAVLOG_STORAGE_ROOT", ".autonavlog-data")),
        weather_mode=cast(WeatherMode, raw_weather_mode),
        msm_cache_dir=(
            None
            if not os.environ.get("AUTONAVLOG_MSM_CACHE")
            else Path(os.environ["AUTONAVLOG_MSM_CACHE"])
        ),
        terrain_cache_path=(
            None
            if not os.environ.get("AUTONAVLOG_TERRAIN_CACHE")
            else Path(os.environ["AUTONAVLOG_TERRAIN_CACHE"])
        ),
        trusted_local_identity=(
            os.environ.get("AUTONAVLOG_TRUSTED_LOCAL_IDENTITY", "").strip() or None
        ),
    )


def create_app(
    config: WebRuntimeConfig | None = None,
    *,
    web_application: AutoNavLogWebApplication | None = None,
) -> FastAPI:
    web = web_application or build_web_application(config or _environment_config())
    app = FastAPI(
        title="AutoNavLog Web",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.web_application = web
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(WebApplicationError)
    async def web_error_handler(
        _request: Request,
        error: WebApplicationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/api/session")
    def create_session(request: Request, response: Response) -> dict[str, Any]:
        session = web.create_session(_owner_identity(request))
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=session.token,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return {"state": web.present(session)}

    @app.delete("/api/session", status_code=204)
    def delete_session(request: Request) -> Response:
        owner_id = _owner_identity(request)
        session_token = request.cookies.get(SESSION_COOKIE_NAME)
        if session_token:
            web.invalidate_session(session_token, owner_id)
        response = Response(status_code=204)
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/api/state")
    def state(session: SessionDependency) -> dict[str, Any]:
        return web.present(session)

    @app.post("/api/import", response_model=None)
    def import_route(
        payload: ImportRouteRequest,
        session: SessionDependency,
    ) -> dict[str, Any] | JSONResponse:
        try:
            if payload.kml_text is not None:
                if len(payload.kml_text.encode("utf-8")) > MAX_UPLOAD_BYTES:
                    raise WebApplicationError(
                        "UPLOAD_TOO_LARGE",
                        "KML/KMZは10 MiB以下にしてください。",
                        status_code=413,
                    )
                result = import_kml_text(payload.kml_text, filename=payload.filename)
            else:
                encoded = payload.content_base64 or ""
                if len(encoded) > MAX_BASE64_CHARACTERS:
                    raise WebApplicationError(
                        "UPLOAD_TOO_LARGE",
                        "KML/KMZは10 MiB以下にしてください。",
                        status_code=413,
                    )
                try:
                    content = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise WebApplicationError(
                        "UPLOAD_ENCODING_INVALID",
                        "アップロード内容を読み取れません。",
                    ) from error
                if len(content) > MAX_UPLOAD_BYTES:
                    raise WebApplicationError(
                        "UPLOAD_TOO_LARGE",
                        "KML/KMZは10 MiB以下にしてください。",
                        status_code=413,
                    )
                result = import_kml_or_kmz(
                    content,
                    filename=payload.filename,
                    kmz_kml_filename=payload.kmz_kml_filename,
                )
        except KmlDocumentSelectionRequired as error:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "KMZ_DOCUMENT_SELECTION_REQUIRED",
                        "message": "KMZ内で使用するKMLを選択してください。",
                        "candidates": list(error.candidates),
                    }
                },
            )
        except KmlImportError as error:
            raise WebApplicationError("KML_IMPORT_FAILED", str(error)) from error
        return web.accept_import(session, result=result, filename=payload.filename)

    @app.post("/api/route/confirm")
    def confirm_route(
        payload: ConfirmRouteRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return web.confirm_route(session, payload)

    @app.put("/api/project")
    def update_project(
        payload: UpdateProjectRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return web.update_project(session, payload)

    @app.post("/api/calculate")
    def calculate(session: SessionDependency) -> dict[str, Any]:
        return web.calculate(session)

    @app.put("/api/acknowledgements/{ack_key}")
    def acknowledge(
        ack_key: str,
        payload: AcknowledgeRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return web.acknowledge(session, ack_key, payload.checked)

    @app.post("/api/projects/save")
    def save_project(
        payload: SaveProjectRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return web.save(session, payload)

    @app.post("/api/projects/load")
    def load_project(
        payload: LoadProjectRequest,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return web.load(session, payload.project_id)

    @app.post("/api/snapshots")
    def create_snapshot(session: SessionDependency) -> dict[str, str]:
        return {"snapshot": web.create_snapshot(session)}

    @app.get("/api/transfer-aid")
    def transfer_aid(session: SessionDependency) -> HTMLResponse:
        filename, html = web.transfer_aid_html(session)
        return HTMLResponse(
            content=html,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    static_root = Path(__file__).with_name("static")
    assets_root = static_root / "assets"
    if assets_root.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_root), name="web-assets")

    @app.get("/{requested_path:path}", include_in_schema=False)
    def frontend(requested_path: str) -> Response:
        if requested_path == "api" or requested_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={
                    "error": {
                        "code": "API_NOT_FOUND",
                        "message": "指定されたAPIはありません。",
                    }
                },
            )
        if requested_path and "/" not in requested_path:
            candidate = static_root / requested_path
            if candidate.is_file() and candidate.parent == static_root:
                return FileResponse(candidate)
        index = static_root / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>AutoNavLog Web</h1>"
            "<p>Frontend assets are not built. Run npm --prefix web run build.</p>",
            status_code=503,
        )

    return app
