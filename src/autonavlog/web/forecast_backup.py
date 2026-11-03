"""Capability-scoped, bounded calculation-only access; never Legacy editing access."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from autonavlog.application.project_fingerprints import forecast_selection_fingerprint
from autonavlog.forecast_backup import (
    BackupRequest,
    BackupResult,
    calculation_contract,
    calculation_project,
    canonical_json,
    input_identity,
)
from autonavlog.forecast_backup_messages import MESSAGES
from autonavlog.local import LocalApplication
from autonavlog.weather.complete_forecast import CompleteForecastProvider
from autonavlog.web.facade import WebApplicationError

PREFIX = "/api/forecast-backup/jobs/"
MAX_REQUEST = 256 * 1024
MAX_RESULT = 2 * 1024 * 1024
RETENTION_SECONDS = 86400
REQUEST_SECONDS = 600


def failure(code: str, message: str, status: int = 400) -> WebApplicationError:
    return WebApplicationError(code, MESSAGES[message], status_code=status)


class BackupJobs:
    """Bounded ledger: stable IDs survive response loss and process restart.

    Requests are accepted for ten minutes and their tombstones/results live one day.
    A lost running process is never restarted by a retry. At its deadline the job
    reports interruption; a caller may explicitly initiate a new calculation.
    """

    def __init__(
        self,
        path: Path,
        compute: Callable[[BackupRequest], BackupResult],
        *,
        maximum: int = 64,
        per_owner: int = 8,
    ) -> None:
        self.path, self.compute = path, compute
        self.maximum, self.per_owner = maximum, per_owner
        path.parent.mkdir(parents=True, exist_ok=True)
        key_path = path.with_suffix(".key")
        try:
            with key_path.open("xb") as stream:
                stream.write(secrets.token_bytes(32))
            key_path.chmod(0o600)
        except FileExistsError:
            pass
        self.client_key = key_path.read_bytes()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="forecast-backup")
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                owner TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL,
                created REAL NOT NULL, status TEXT NOT NULL, result TEXT, error TEXT,
                PRIMARY KEY(owner,id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS admissions (
                owner TEXT NOT NULL, id TEXT NOT NULL, client TEXT NOT NULL,
                created REAL NOT NULL, PRIMARY KEY(owner,id))""")
            db.execute("""CREATE TABLE IF NOT EXISTS rates (
                client TEXT NOT NULL, minute INTEGER NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(client,minute))""")

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def client_identity(self, address: str) -> str:
        # Retain no raw network address. A persistent random key prevents dictionary lookup.
        return hmac.new(self.client_key, address.encode(), hashlib.sha256).hexdigest()

    def limit_requests(self, client: str) -> None:
        minute = int(time.time() // 60)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM rates WHERE minute < ?", (minute - 1,))
            row = db.execute(
                "SELECT count FROM rates WHERE client=? AND minute=?", (client, minute)
            ).fetchone()
            if (row and row[0] >= 120) or db.execute("SELECT count(*) FROM rates").fetchone()[
                0
            ] >= 4096:
                raise failure("FORECAST_BACKUP_BUSY", "busy", 429)
            db.execute(
                "INSERT INTO rates VALUES (?,?,1) ON CONFLICT(client,minute) "
                "DO UPDATE SET count=count+1",
                (client, minute),
            )

    def submit(
        self, owner: str, request: BackupRequest, client: str | None = None
    ) -> dict[str, Any]:
        body = request.model_dump(mode="json")
        digest = hashlib.sha256(canonical_json(body).encode()).hexdigest()
        identity = str(request.request_id)
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM jobs WHERE created < ?", (now - RETENTION_SECONDS,))
            db.execute("DELETE FROM admissions WHERE created < ?", (now - RETENTION_SECONDS,))
            existing = db.execute(
                "SELECT digest FROM jobs WHERE owner=? AND id=?", (owner, identity)
            ).fetchone()
            if existing:
                if existing["digest"] != digest:
                    raise failure("FORECAST_BACKUP_CONFLICT", "request_conflict", 409)
            else:
                age = now - request.issued_at.timestamp()
                if age < -300 or age > REQUEST_SECONDS:
                    raise failure("FORECAST_BACKUP_EXPIRED", "request_expired", 410)
                if client is not None:
                    recent = db.execute(
                        "SELECT count(*) FROM admissions WHERE client=? AND created>?",
                        (client, now - 600),
                    ).fetchone()[0]
                    daily = db.execute(
                        "SELECT count(*) FROM admissions WHERE client=?", (client,)
                    ).fetchone()[0]
                    active = db.execute(
                        "SELECT count(*) FROM jobs WHERE status='running' AND created>?",
                        (now - REQUEST_SECONDS,),
                    ).fetchone()[0]
                    own_active = db.execute(
                        "SELECT count(*) FROM jobs j JOIN admissions a "
                        "ON j.owner=a.owner AND j.id=a.id WHERE a.client=? "
                        "AND j.status='running' AND j.created>?",
                        (client, now - REQUEST_SECONDS),
                    ).fetchone()[0]
                    if recent >= 6 or daily >= 20 or active >= 8 or own_active >= 2:
                        raise failure("FORECAST_BACKUP_BUSY", "busy", 429)
                total = db.execute("SELECT count(*) FROM jobs").fetchone()[0]
                own = db.execute("SELECT count(*) FROM jobs WHERE owner=?", (owner,)).fetchone()[0]
                if total >= self.maximum or own >= self.per_owner:
                    raise failure("FORECAST_BACKUP_BUSY", "busy", 429)
                db.execute(
                    "INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                    (
                        owner,
                        identity,
                        digest,
                        now,
                        "running",
                        None,
                        None,
                    ),
                )
                if client is not None:
                    db.execute(
                        "INSERT INTO admissions VALUES (?,?,?,?)", (owner, identity, client, now)
                    )
        if existing is None:
            try:
                self.executor.submit(self._run, owner, request)
            except RuntimeError:
                self._finish(owner, identity, None, "FORECAST_BACKUP_INTERRUPTED")
        return self.get(owner, identity)

    def _finish(self, owner: str, identity: str, result: str | None, error: str | None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,created FROM jobs WHERE owner=? AND id=?", (owner, identity)
            ).fetchone()
            if row is None or row["status"] != "running":
                return
            if row["created"] < time.time() - REQUEST_SECONDS:
                result, error = None, "FORECAST_BACKUP_INTERRUPTED"
            db.execute(
                "UPDATE jobs SET status=?,result=?,error=? "
                "WHERE owner=? AND id=? AND status='running'",
                ("succeeded" if result else "failed", result, error, owner, identity),
            )

    def _run(self, owner: str, request: BackupRequest) -> None:
        # A queued task can expire or be reported interrupted before a worker is free.
        # Check the ledger before spending any acquisition/calculation resources.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status,created FROM jobs WHERE owner=? AND id=?",
                (owner, str(request.request_id)),
            ).fetchone()
            if row is None or row["status"] != "running":
                return
            if row["created"] < time.time() - REQUEST_SECONDS:
                db.execute(
                    "UPDATE jobs SET status='failed',error='FORECAST_BACKUP_INTERRUPTED' "
                    "WHERE owner=? AND id=?",
                    (owner, str(request.request_id)),
                )
                return
        try:
            result = self.compute(request).model_dump_json()
            if len(result.encode()) > MAX_RESULT:
                raise ValueError("result exceeds limit")
        except Exception:
            # Never persist credentials, upstream URLs, raw exceptions or stack traces.
            self._finish(owner, str(request.request_id), None, "FORECAST_BACKUP_FAILED")
        else:
            self._finish(owner, str(request.request_id), result, None)

    def get(self, owner: str, identity: str) -> dict[str, Any]:
        now = time.time()
        with self.connect() as db:
            db.execute("DELETE FROM jobs WHERE created < ?", (now - RETENTION_SECONDS,))
            db.execute("DELETE FROM admissions WHERE created < ?", (now - RETENTION_SECONDS,))
            db.execute(
                "UPDATE jobs SET status='failed',error='FORECAST_BACKUP_INTERRUPTED' "
                "WHERE owner=? AND id=? AND status='running' AND created < ?",
                (owner, identity, now - REQUEST_SECONDS),
            )
            row = db.execute(
                "SELECT * FROM jobs WHERE owner=? AND id=?", (owner, identity)
            ).fetchone()
        if row is None:
            raise failure("FORECAST_BACKUP_NOT_FOUND", "job_not_found", 404)
        return {
            "request_id": identity,
            "status": row["status"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "error": row["error"],
        }

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)


def calculator(
    data_root: Path, weather_factory: Callable[[], Any], destination_wind_provider: Any
) -> Callable[[BackupRequest], BackupResult]:
    def calculate(request: BackupRequest) -> BackupResult:
        local = LocalApplication(data_root)
        try:
            if request.contract != calculation_contract(data_root):
                raise failure("FORECAST_BACKUP_INCOMPATIBLE", "incompatible", 409)
            project = request.project.model_copy(deep=True)
            if (
                project.weather_mode != "FORECAST"
                or project != calculation_project(project)
                or request.input_identity
                != input_identity(project, request.rounding_mode, request.refresh_forecast)
            ):
                raise failure("FORECAST_BACKUP_INVALID", "invalid_result")
            local.session.project = project
            local.session.rounding_mode = request.rounding_mode
            local.session.weather_provider = CompleteForecastProvider(weather_factory())
            local.app.destination_wind_provider = destination_wind_provider
            local.session.forecast_update_fingerprint = (
                forecast_selection_fingerprint(project, rounding_mode=request.rounding_mode)
                if request.refresh_forecast
                else None
            )
            local.validate_calculation_input({"rounding_mode": request.rounding_mode})
            local.dispatch("calculate", {"rounding_mode": request.rounding_mode})
            last = local.session.last_calculation
            if last is None or last.outcome.blockers:
                raise failure("FORECAST_BACKUP_FAILED", "backup_unavailable")
            return BackupResult(
                contract=request.contract,
                request_id=request.request_id,
                input_identity=request.input_identity,
                rounding_mode=request.rounding_mode,
                refresh_available=local.session.forecast_update_fingerprint is not None,
                calculation=last,
                calculation_sha256=hashlib.sha256(
                    canonical_json(last.model_dump(mode="json")).encode()
                ).hexdigest(),
            )
        finally:
            local.close()

    return calculate


def install_backup_routes(
    app: FastAPI,
    jobs: BackupJobs,
    contract: Any,
    allowed_origin: str,
    trusted_proxy_networks: tuple[str, ...] = (),
) -> None:
    parsed = urlsplit(allowed_origin)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
    ):
        raise ValueError(
            "Backup origin must be an exact HTTPS origin (HTTP loopback only for tests)"
        )
    networks = tuple(ipaddress.ip_network(value) for value in trusted_proxy_networks)

    def client(request: Request) -> str:
        address = request.client.host if request.client else "unknown"
        try:
            peer = ipaddress.ip_address(address)
            if any(peer in network for network in networks):
                # Enable only behind a gateway that overwrites this header and whose
                # origin cannot be reached by untrusted clients (deployment acceptance).
                address = str(ipaddress.ip_address(request.headers.get("CF-Connecting-IP", "")))
        except ValueError:
            if networks:
                raise failure("FORECAST_BACKUP_INVALID", "invalid_result", 400) from None
        try:
            ip = ipaddress.ip_address(address)
            if ip.version == 6:
                address = str(ipaddress.ip_network(f"{ip}/64", strict=False))
        except ValueError:
            pass
        return jobs.client_identity(address)

    @app.middleware("http")
    async def backup_boundary(request: Request, call_next: Any) -> Any:
        if not request.url.path.startswith(PREFIX):
            return await call_next(request)
        origin = request.headers.get("Origin")
        if origin != allowed_origin:
            return JSONResponse(
                {"error": {"code": "ORIGIN_DENIED", "message": MESSAGES["origin_denied"]}}, 403
            )
        response = JSONResponse({}) if request.method == "OPTIONS" else await call_next(request)
        response.headers.update(
            {
                "Access-Control-Allow-Origin": allowed_origin,
                "Access-Control-Allow-Headers": "Content-Type, X-AutoNavLog-Request",
                "Access-Control-Allow-Methods": "GET, PUT, OPTIONS",
                "Vary": "Origin",
                "Cache-Control": "no-store",
            }
        )
        return response

    def owner(request: Request) -> str:
        capability = request.headers.get("X-AutoNavLog-Request", "")
        if not re.fullmatch(r"[a-f0-9]{64}", capability):
            raise failure("FORECAST_BACKUP_NOT_FOUND", "job_not_found", 404)
        jobs.limit_requests(client(request))
        # Capability is never in a URL, database, result or diagnostic.
        return hashlib.sha256(capability.encode()).hexdigest()

    @app.get(PREFIX + "{request_id}")
    def get_job(request_id: UUID, request: Request) -> dict[str, Any]:
        return jobs.get(owner(request), str(request_id))

    @app.put(PREFIX + "{request_id}", status_code=202)
    async def put_job(request_id: UUID, request: Request) -> dict[str, Any]:
        owned = await run_in_threadpool(owner, request)
        body = bytearray()
        import asyncio

        try:
            async with asyncio.timeout(10):
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > MAX_REQUEST:
                        raise failure("FORECAST_BACKUP_INVALID", "invalid_result", 413)
        except TimeoutError:
            raise failure("FORECAST_BACKUP_EXPIRED", "request_expired", 408) from None
        try:
            payload = BackupRequest.model_validate_json(body)
        except ValueError as error:
            raise failure("FORECAST_BACKUP_INVALID", "invalid_result", 422) from error
        if payload.request_id != request_id:
            raise failure("FORECAST_BACKUP_CONFLICT", "request_conflict", 409)
        if payload.contract != contract:
            raise failure("FORECAST_BACKUP_INCOMPATIBLE", "incompatible", 409)
        return await run_in_threadpool(jobs.submit, owned, payload, client(request))
