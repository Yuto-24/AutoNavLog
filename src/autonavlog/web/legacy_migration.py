"""Durable one-to-one linking and resumable, owner-serialized Legacy activation."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from autonavlog.local_persistence import LocalProjectRecord
from autonavlog.storage.local import LocalProjectRepository
from autonavlog.storage.repository import owner_storage_key

from .facade import AutoNavLogWebApplication, WebApplicationError
from .migration_remote import GoogleIdentity
from .models import WorkingCalculation

BATCH_SIZE = 25
BATCH_BYTES = 6 * 1024 * 1024
LEASE_SECONDS = 180


class MigrationRemote(Protocol):
    def read(self, ids: list[str]) -> dict[str, dict[str, Any]]: ...
    def commit(self, writes: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None: ...


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def content(value: dict[str, Any]) -> str:
    """Ignore transport/local CAS identities, but compare every durable domain field."""
    record = json.loads(value["payload"])
    record.pop("token", None)
    return canonical(
        {
            "record": record,
            "deletion": value["deletion"],
            "undoUntil": value["undoUntil"],
            "latestDeviceId": value["latestDeviceId"],
        }
    )


def conflict() -> WebApplicationError:
    return WebApplicationError(
        "MIGRATION_CONFLICT",
        "同期先に異なる更新があります。Legacyは利用できます。移行を完了できません。",
        status_code=409,
    )


class LegacyMigration:
    def __init__(self, path: Path, web: AutoNavLogWebApplication, navmate_url: str) -> None:
        self.path = path
        self.web = web
        self.navmate_url = navmate_url
        self._locks: dict[str, RLock] = {}
        self._locks_lock = RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS links (
                owner TEXT PRIMARY KEY, account TEXT UNIQUE NOT NULL, subject TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL, run TEXT, expires REAL, plan TEXT NOT NULL DEFAULT '[]',
                completed INTEGER NOT NULL DEFAULT 0, error TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                owner TEXT NOT NULL, id TEXT NOT NULL, previous TEXT, intended TEXT NOT NULL,
                PRIMARY KEY(owner,id))""")

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def lock(self, owner: str) -> RLock:
        with self._locks_lock:
            return self._locks.setdefault(owner, RLock())

    def _row(self, owner: str) -> dict[str, Any] | None:
        with self.db() as db:
            db.execute(
                "UPDATE links SET state='LINKED', run=NULL, error=? "
                "WHERE owner=? AND state='MIGRATING' AND expires < ?",
                ("移行が中断されました。再試行できます。", owner, time.time()),
            )
            row = db.execute("SELECT * FROM links WHERE owner=?", (owner,)).fetchone()
            return dict(row) if row else None

    def status(self, owner: str) -> dict[str, Any]:
        with self.lock(owner):
            row = self._row(owner)
            return self.public(row)

    def public(self, row: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "state": row["state"] if row else "UNLINKED",
            "completed": row["completed"] if row else 0,
            "total": len(json.loads(row["plan"])) if row else 0,
            "error": row["error"] if row else None,
            "navmateUrl": self.navmate_url,
        }

    @contextmanager
    def legacy_operation(self, identity: str) -> Iterator[None]:
        owner = owner_storage_key(identity)
        with self.lock(owner):
            row = self._row(owner)
            if row and row["state"] in {"MIGRATING", "NAVMATE_ACTIVE"}:
                raise WebApplicationError(
                    "LEGACY_" + row["state"],
                    "NavMateへ移行中です。"
                    if row["state"] == "MIGRATING"
                    else "NavMateへ移行済みです。NavMateを開いてください。",
                    status_code=423,
                )
            yield

    def link(self, owner_identity: str, identity: GoogleIdentity) -> dict[str, Any]:
        owner = owner_storage_key(owner_identity)
        with self.lock(owner), self.db() as db:
            # Serialize uniqueness checks across different Legacy owners as well.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM links WHERE owner=? OR account=? OR subject=?",
                (owner, identity.account_id, identity.subject),
            ).fetchall()
            if existing:
                if len(existing) != 1 or (
                    existing[0]["owner"],
                    existing[0]["account"],
                    existing[0]["subject"],
                ) != (owner, identity.account_id, identity.subject):
                    raise WebApplicationError(
                        "ACCOUNT_ALREADY_LINKED",
                        "別の所有者またはGoogleアカウントに紐付け済みです。",
                        status_code=409,
                    )
            else:
                db.execute(
                    "INSERT INTO links(owner,account,subject,state) VALUES(?,?,?,'LINKED')",
                    (owner, identity.account_id, identity.subject),
                )
        return self.status(owner)

    def account_owner(self, identity: GoogleIdentity) -> str | None:
        with self.db() as db:
            row = db.execute(
                "SELECT owner FROM links WHERE account=? AND subject=?",
                (identity.account_id, identity.subject),
            ).fetchone()
            return row["owner"] if row else None

    def snapshot(self, owner: str) -> list[dict[str, Any]]:
        repository = self.web.project_service.repository
        if not isinstance(repository, LocalProjectRepository):
            raise ValueError("Legacy storage unavailable")
        row = self._row(owner)
        assert row is not None
        records = []
        for summary in repository.list_projects():
            if not summary.web_owner_id or owner_storage_key(summary.web_owner_id) != owner:
                continue
            draft = repository.load(summary.id)
            checkpoint = repository.load_checkpoint(summary.id)
            last = repository.load_last_calculation(summary.id, owner_key=owner)
            for project in [draft, checkpoint, last.project if last else None]:
                if project is not None:
                    stored_owner = project.metadata.get("web_owner_id")
                    if (
                        not isinstance(stored_owner, str)
                        or owner_storage_key(stored_owner) != owner
                    ):
                        raise ValueError("Project ownership mismatch")
                    project.metadata.pop("web_owner_id")
            calculation = (
                None
                if last is None
                else WorkingCalculation(
                    project=last.project,
                    outcome=last.outcome,
                    destination_wind=last.destination_wind,
                    forecast_metadata=last.forecast_metadata,
                    calculation_fingerprint=last.calculation_fingerprint,
                )
            )
            if calculation is not None:
                # Reuse the same Python fingerprint check as #185 prepareSyncResolution.
                session = self.web.create_session(
                    summary.web_owner_id, restore_persisted=False, persist_working=False
                )
                try:
                    ui = session.readiness_service.ui_state(draft)
                    fingerprint = (
                        session.readiness_service.fingerprints(
                            draft, calculation.outcome, ui
                        ).calculation_input
                        if ui
                        else None
                    )
                    if fingerprint != calculation.calculation_fingerprint:
                        calculation = None
                finally:
                    self.web.invalidate_session(session.token, session.owner_id)
            record = LocalProjectRecord(
                schemaVersion=2,
                id=draft.id,
                token=uuid4(),
                draft=draft,
                checkpoint=checkpoint,
                lastCalculation=calculation,
                updatedAt=draft.updated_at,
            )
            record.token = uuid5(
                NAMESPACE_URL, canonical(record.model_dump(mode="json", exclude={"token"}))
            )
            records.append(
                {
                    "id": str(record.id),
                    "payload": record.model_dump_json(),
                    "deletion": "ACTIVE",
                    "undoUntil": None,
                    "latestDeviceId": None
                    if checkpoint
                    else "legacy-import-" + str(uuid5(NAMESPACE_URL, row["account"])),
                }
            )
        return records

    def begin(self, owner: str) -> dict[str, Any]:
        with self.lock(owner):
            row = self._row(owner)
            assert row is not None
            if row["state"] != "LINKED":
                return self.public(row)
            # No Legacy operation can enter while the snapshot is taken. Existing
            # calculations hold the same owner lock until their persistence completes.
            plan = self.snapshot(owner)
            ids = {value["id"] for value in plan}
            with self.db() as db:
                # A retry must also retire a partial import deleted/replaced in Legacy.
                for receipt in db.execute("SELECT * FROM receipts WHERE owner=?", (owner,)):
                    if receipt["id"] not in ids:
                        value = json.loads(receipt["intended"])
                        value.update(deletion="DELETED", undoUntil=None)
                        plan.append(value)
                db.execute(
                    "UPDATE links SET state='MIGRATING', run=?, expires=?, plan=?, "
                    "completed=0,error=NULL WHERE owner=?",
                    (str(uuid4()), time.time() + LEASE_SECONDS, canonical(plan), owner),
                )
            return self.status(owner)

    def fail(self, owner: str) -> None:
        with self.db() as db:
            db.execute(
                "UPDATE links SET state='LINKED',run=NULL,error=? WHERE owner=? "
                "AND state='MIGRATING'",
                ("引継ぎを完了できませんでした。Legacyは利用できます。再試行してください。", owner),
            )

    def step(self, owner: str, remote: MigrationRemote) -> dict[str, Any]:
        with self.lock(owner):
            row = self._row(owner)
            assert row is not None
            if row["state"] != "MIGRATING":
                return self.public(row)
            try:
                plan = json.loads(row["plan"])
                start = row["completed"]
                batch = plan[start : start + BATCH_SIZE]
                # Bound normal 200-Project imports by Firestore's commit size as well as count.
                size = 0
                for index, value in enumerate(batch):
                    size += len(canonical(value).encode("utf-8"))
                    if index and size > BATCH_BYTES:
                        batch = batch[:index]
                        break
                if batch:
                    current = remote.read([value["id"] for value in batch])
                    writes = []
                    with self.db() as db:
                        for target in batch:
                            id = target["id"]
                            old = current.get(id)
                            receipt = db.execute(
                                "SELECT * FROM receipts WHERE owner=? AND id=?", (owner, id)
                            ).fetchone()
                            if old and content(old) == content(target):
                                desired = {
                                    key: value for key, value in old.items() if key != "_updateTime"
                                }
                            else:
                                if old and (
                                    receipt is None
                                    or not any(
                                        candidate
                                        and content(old) == content(json.loads(candidate))
                                        and old["version"] == json.loads(candidate)["version"]
                                        for candidate in [receipt["previous"], receipt["intended"]]
                                    )
                                ):
                                    raise conflict()
                                if (
                                    old
                                    and old["deletion"] != "ACTIVE"
                                    and target["deletion"] != "DELETED"
                                ):
                                    raise conflict()
                                desired = {
                                    **target,
                                    "schema": 1,
                                    "version": str(uuid4()),
                                    "revision": old["revision"] + 1 if old else 1,
                                    "baseVersion": old["version"] if old else None,
                                }
                                writes.append((desired, old))
                            # Journal BEFORE the remote call: a lost acknowledgement
                            # can be distinguished from an independent remote edit.
                            db.execute(
                                "INSERT INTO receipts VALUES(?,?,?,?) ON CONFLICT(owner,id) "
                                "DO UPDATE SET previous=excluded.previous,"
                                "intended=excluded.intended",
                                (owner, id, canonical(old) if old else None, canonical(desired)),
                            )
                    remote.commit(writes)
                    verified = remote.read([value["id"] for value in batch])
                    if any(
                        value["id"] not in verified
                        or content(verified[value["id"]]) != content(value)
                        for value in batch
                    ):
                        raise conflict()
                    with self.db() as db:
                        db.execute(
                            "UPDATE links SET completed=?,expires=? WHERE owner=?",
                            (start + len(batch), time.time() + LEASE_SECONDS, owner),
                        )
                else:
                    # Verify ALL records again before the one-way authority switch.
                    verified = remote.read([value["id"] for value in plan])
                    if any(
                        value["id"] not in verified
                        or content(verified[value["id"]]) != content(value)
                        for value in plan
                    ):
                        raise conflict()
                    with self.db() as db:
                        db.execute(
                            "UPDATE links SET state='NAVMATE_ACTIVE',run=NULL,error=NULL "
                            "WHERE owner=?",
                            (owner,),
                        )
                return self.status(owner)
            except Exception:
                self.fail(owner)
                raise
