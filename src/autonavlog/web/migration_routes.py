"""The link route requires Access AND Google; activation routes require Google only."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from autonavlog.storage.repository import owner_storage_key

from .facade import WebApplicationError
from .legacy_migration import LegacyMigration
from .migration_remote import FirebaseTokenVerifier, FirestoreMigrationRemote, GoogleIdentity

LOGGER = logging.getLogger(__name__)


def install_migration_routes(
    app: FastAPI,
    migration: LegacyMigration,
    verifier: FirebaseTokenVerifier,
    owner_identity: Callable[[Request], str],
) -> None:
    navmate_origin_parts = urlsplit(migration.navmate_url)
    navmate_origin = f"{navmate_origin_parts.scheme}://{navmate_origin_parts.netloc}"

    def google(request: Request) -> tuple[GoogleIdentity, str]:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            raise WebApplicationError(
                "GOOGLE_AUTH_REQUIRED", "Googleログインが必要です。", status_code=401
            )
        token = authorization[7:]
        try:
            return verifier.verify(token), token
        except Exception as error:
            raise WebApplicationError(
                "GOOGLE_AUTH_INVALID",
                "Google認証を確認できません。再ログインしてください。",
                status_code=401,
            ) from error

    @app.middleware("http")
    async def migration_boundary(request: Request, call_next: Any) -> Any:
        path = request.url.path
        activation = path.startswith("/api/navmate-migration/")
        origin = request.headers.get("Origin")
        if activation and origin and origin != navmate_origin:
            return JSONResponse(
                {"error": {"code": "ORIGIN_DENIED", "message": "Origin denied"}}, 403
            )
        if activation and request.method == "OPTIONS":
            response = JSONResponse({})
        else:
            # Navigation redirect is based only on the signed Legacy identity.
            if request.method == "GET" and "text/html" in request.headers.get("Accept", ""):
                try:
                    status = await run_in_threadpool(
                        lambda: migration.status(owner_storage_key(owner_identity(request)))
                    )
                    if status["state"] == "NAVMATE_ACTIVE":
                        return RedirectResponse(
                            migration.navmate_url,
                            status_code=303,
                            headers={"Cache-Control": "no-store"},
                        )
                except WebApplicationError:
                    pass
            response = await call_next(request)
        if activation and origin == navmate_origin:
            response.headers["Access-Control-Allow-Origin"] = navmate_origin
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/api/account-link/config")
    def link_config(request: Request) -> dict[str, str]:
        owner_identity(request)
        return {
            "apiKey": verifier.api_key,
            "projectId": verifier.project,
            "authDomain": (
                os.environ.get("AUTONAVLOG_FIREBASE_AUTH_DOMAIN")
                or verifier.project + ".firebaseapp.com"
            ),
            "appId": os.environ.get("AUTONAVLOG_FIREBASE_APP_ID", ""),
        }

    @app.get("/api/account-link")
    def link_status(request: Request) -> dict[str, Any]:
        return migration.status(owner_storage_key(owner_identity(request)))

    @app.post("/api/account-link")
    def link(request: Request) -> dict[str, Any]:
        # Trusted local identity is NEVER sufficient to create an ownership link.
        if not request.headers.get("Cf-Access-Jwt-Assertion"):
            raise WebApplicationError(
                "ACCESS_REQUIRED", "署名付きCloudflare認証が必要です。", status_code=401
            )
        link_origin = urlsplit(request.headers.get("Origin", ""))
        if (
            link_origin.scheme != "https"
            or link_origin.netloc != request.headers.get("Host")
            or link_origin.path
            or link_origin.query
            or link_origin.fragment
        ):
            raise WebApplicationError(
                "ORIGIN_DENIED", "旧サイトから紐付けを開始してください。", status_code=403
            )
        owner = owner_identity(request)
        identity, _ = google(request)
        return migration.link(owner, identity)

    @app.get("/api/navmate-migration/status")
    def activation_status(request: Request) -> dict[str, Any]:
        identity, _ = google(request)
        owner = migration.account_owner(identity)
        return migration.public(None) if owner is None else migration.status(owner)

    @app.post("/api/navmate-migration/{operation}")
    def activation(request: Request, operation: str) -> dict[str, Any]:
        identity, token = google(request)
        owner = migration.account_owner(identity)
        if owner is None:
            return migration.public(None)
        try:
            if operation == "begin":
                return migration.begin(owner)
            if operation == "step":
                return migration.step(
                    owner, FirestoreMigrationRemote(verifier.project, identity, token)
                )
            raise WebApplicationError("NOT_FOUND", "操作がありません。", status_code=404)
        except WebApplicationError:
            raise
        except Exception as error:
            LOGGER.warning("Legacy activation failed: %s", type(error).__name__)
            raise WebApplicationError(
                "MIGRATION_FAILED",
                "引継ぎを完了できませんでした。Legacyは利用できます。再試行してください。",
                status_code=503,
            ) from error
