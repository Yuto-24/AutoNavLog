from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from threading import Event
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from autonavlog.storage.repository import owner_storage_key
from autonavlog.web.app import create_app
from autonavlog.web.facade import WebApplicationError
from autonavlog.web.legacy_migration import BATCH_SIZE, LegacyMigration, canonical
from autonavlog.web.migration_remote import FirebaseTokenVerifier, GoogleIdentity
from autonavlog.web.runtime import WebRuntimeConfig, build_web_application

ROOT = Path(__file__).resolve().parents[2]
OWNER = "pilot@example.com"
KEY = owner_storage_key(OWNER)
GOOGLE = GoogleIdentity("google-subject", "account-A")


class Remote:
    def __init__(self):
        self.rows = {}
        self.commits = 0
        self.reads = 0
        self.lose_ack = False
        self.fail_read = False

    def read(self, ids):
        self.reads += 1
        if self.fail_read:
            raise OSError("offline")
        return {id: copy.deepcopy(self.rows[id]) for id in ids if id in self.rows}

    def commit(self, writes):
        self.commits += bool(writes)
        for value, old in writes:
            assert self.rows.get(value["id"]) == old
        for value, _old in writes:
            self.rows[value["id"]] = copy.deepcopy(value)
        if self.lose_ack:
            self.lose_ack = False
            raise OSError("lost acknowledgement")


@pytest.fixture
def migration(tmp_path):
    web = build_web_application(
        WebRuntimeConfig(
            data_root=ROOT / "data", storage_root=tmp_path, trusted_local_identity=OWNER
        )
    )
    service = LegacyMigration(tmp_path / "links.sqlite3", web, "https://navmate.example/")
    web.owner_operation = service.legacy_operation
    return service


def project(migration, *, owner=OWNER, saved=True, name="checkpoint"):
    service = migration.web.project_service
    value = service.create(
        name=name,
        flight_date=date(2026, 9, 18),
        planned_departure_time_jst=datetime(2026, 9, 18, 9, tzinfo=ZoneInfo("Asia/Tokyo")),
        departure_airport_id="RJFM",
        destination_airport_id="RJFO",
        total_usable_fuel_gal=90,
        default_variation_deg_east=-8,
    )
    value.metadata["web_owner_id"] = owner
    if saved:
        value = service.repository.save(value, 0).project
    service.repository.autosave(value, set_last_opened=True)
    return value


def finish(migration, remote):
    status = migration.begin(KEY)
    while status["state"] == "MIGRATING":
        status = migration.step(KEY, remote)
    return status


def test_link_is_durable_idempotent_one_to_one_and_does_not_copy_or_lock(migration):
    value = project(migration)
    assert migration.status(KEY)["state"] == "UNLINKED"
    assert migration.link(OWNER, GOOGLE)["state"] == "LINKED"
    assert migration.link(OWNER, GOOGLE)["state"] == "LINKED"
    with migration.legacy_operation(OWNER):
        migration.web.project_service.repository.autosave(value)
    for owner, identity in [
        (OWNER, GoogleIdentity("other", "other")),
        ("other@example.com", GOOGLE),
    ]:
        with pytest.raises(WebApplicationError, match="紐付け"):
            migration.link(owner, identity)
    restarted = LegacyMigration(migration.path, migration.web, migration.navmate_url)
    assert restarted.account_owner(GOOGLE) == KEY
    assert restarted.account_owner(GoogleIdentity("other", GOOGLE.account_id)) is None
    with restarted.db() as db:
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0


def test_all_saved_draft_latest_union_identity_and_200_project_batching(migration):
    saved = [project(migration, name=f"saved-{index}") for index in range(199)]
    latest = project(migration, saved=False)
    other = project(migration, owner="other@example.com")
    modified = saved[0].model_copy(deep=True)
    modified.pilot_name = "unsaved edit"
    migration.web.project_service.repository.autosave(modified)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    remote.rows["unrelated"] = {"must": "remain unchanged"}
    assert finish(migration, remote)["state"] == "NAVMATE_ACTIVE"
    assert len(remote.rows) == 201
    assert str(other.id) not in remote.rows
    assert remote.rows["unrelated"] == {"must": "remain unchanged"}
    assert remote.commits == 200 // BATCH_SIZE
    assert remote.reads == 2 * (200 // BATCH_SIZE) + 1
    result = json.loads(remote.rows[str(saved[0].id)]["payload"])
    assert result["checkpoint"]["pilot_name"] == ""
    assert result["draft"]["pilot_name"] == "unsaved edit"
    assert json.loads(remote.rows[str(latest.id)]["payload"])["checkpoint"] is None
    assert "web_owner_id" not in canonical(remote.rows)
    assert (
        migration.web.project_service.repository.load(saved[0].id).metadata["web_owner_id"] == OWNER
    )
    with pytest.raises(WebApplicationError, match="移行済み"):
        migration.web.create_session(OWNER)


def test_lost_ack_resume_then_legacy_only_update_without_duplicate(migration):
    value = project(migration)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    remote.lose_ack = True
    migration.begin(KEY)
    with pytest.raises(OSError):
        migration.step(KEY, remote)
    assert migration.status(KEY)["state"] == "LINKED"
    previous = copy.deepcopy(remote.rows)
    value.pilot_name = "edited after failure"
    migration.web.project_service.repository.autosave(value)
    assert finish(migration, remote)["state"] == "NAVMATE_ACTIVE"
    assert list(remote.rows) == [str(value.id)]
    assert remote.rows[str(value.id)]["revision"] == previous[str(value.id)]["revision"] + 1
    assert (
        json.loads(remote.rows[str(value.id)]["payload"])["draft"]["pilot_name"] == value.pilot_name
    )


def test_remote_ahead_and_same_id_collision_fail_without_overwriting(migration):
    value = project(migration)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    migration.begin(KEY)
    migration.step(KEY, remote)
    with migration.lock(KEY):
        migration.fail(KEY)
    remote.rows[str(value.id)]["version"] = "other-device"
    payload = json.loads(remote.rows[str(value.id)]["payload"])
    payload["draft"]["pilot_name"] = "remote ahead"
    remote.rows[str(value.id)]["payload"] = canonical(payload)
    before = copy.deepcopy(remote.rows)
    with pytest.raises(WebApplicationError) as error:
        finish(migration, remote)
    assert error.value.code == "MIGRATION_CONFLICT"
    assert migration.status(KEY)["state"] == "LINKED"
    assert remote.rows == before


def test_same_content_partial_import_and_lost_ack_do_not_write_twice(migration):
    project(migration)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    remote.lose_ack = True
    migration.begin(KEY)
    with pytest.raises(OSError):
        migration.step(KEY, remote)
    before = copy.deepcopy(remote.rows)
    assert finish(migration, remote)["state"] == "NAVMATE_ACTIVE"
    assert remote.rows == before
    assert remote.commits == 1


def test_deleted_partial_import_becomes_tombstone_and_latest_replacement_survives(migration):
    old = project(migration, saved=False)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    remote.lose_ack = True
    migration.begin(KEY)
    with pytest.raises(OSError):
        migration.step(KEY, remote)
    new = project(migration, saved=False)
    assert finish(migration, remote)["state"] == "NAVMATE_ACTIVE"
    assert remote.rows[str(old.id)]["deletion"] == "DELETED"
    assert remote.rows[str(new.id)]["deletion"] == "ACTIVE"


def test_failure_and_expired_lease_unlock_legacy_but_do_not_activate(migration):
    project(migration)
    migration.link(OWNER, GOOGLE)
    migration.begin(KEY)
    with pytest.raises(WebApplicationError) as error:
        migration.web.create_session(OWNER)
    assert error.value.code == "LEGACY_MIGRATING"
    remote = Remote()
    remote.fail_read = True
    with pytest.raises(OSError):
        migration.step(KEY, remote)
    assert migration.web.create_session(OWNER)
    migration.begin(KEY)
    with migration.db() as db:
        db.execute("UPDATE links SET expires=0")
    assert migration.status(KEY)["state"] == "LINKED"
    assert migration.web.create_session(OWNER)


def test_begin_waits_for_in_flight_owner_operation_and_other_owner_is_unaffected(migration):
    migration.link(OWNER, GOOGLE)
    entered, release, finished = Event(), Event(), Event()

    def edit():
        with migration.legacy_operation(OWNER):
            entered.set()
            assert release.wait(5)
            project(migration)

    def begin():
        migration.begin(KEY)
        finished.set()

    with ThreadPoolExecutor(2) as executor:
        editing = executor.submit(edit)
        assert entered.wait(5)
        starting = executor.submit(begin)
        assert not finished.wait(0.1)
        assert migration.web.create_session("different@example.com")
        release.set()
        editing.result()
        starting.result()
    assert migration.status(KEY)["total"] == 1


def test_final_verification_detects_update_to_an_earlier_batch(migration):
    value = project(migration)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    migration.begin(KEY)
    migration.step(KEY, remote)
    remote.rows[str(value.id)]["deletion"] = "DELETED"
    with pytest.raises(WebApplicationError):
        migration.step(KEY, remote)
    assert migration.status(KEY)["state"] == "LINKED"


def test_signed_owner_link_google_boundary_cors_and_redirect(migration, monkeypatch):
    monkeypatch.setenv("AUTONAVLOG_FIREBASE_PROJECT_ID", "test-project")
    monkeypatch.setenv("AUTONAVLOG_FIREBASE_API_KEY", "test-key")
    monkeypatch.setenv("AUTONAVLOG_NAVMATE_URL", "https://navmate.example/")

    class Access:
        def verify_email(self, token):
            if token != "signed-owner":
                from autonavlog.web.cloudflare_access import CloudflareAccessVerificationError

                raise CloudflareAccessVerificationError("invalid")
            return OWNER

    migration.web.access_verifier = Access()
    monkeypatch.setattr(
        FirebaseTokenVerifier,
        "verify",
        lambda self, token: (
            GOOGLE if token == "valid-google" else (_ for _ in ()).throw(ValueError("invalid"))
        ),
    )
    app = create_app(web_application=migration.web)
    service = app.state.legacy_migration
    with TestClient(app, base_url="https://legacy.example") as client:
        signed = {
            "Cf-Access-Jwt-Assertion": "signed-owner",
            "Authorization": "Bearer valid-google",
            "Origin": "https://legacy.example",
        }
        assert (
            client.post(
                "/api/account-link", headers={"Authorization": "Bearer valid-google"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/account-link", headers={**signed, "Cf-Access-Jwt-Assertion": "forged"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/account-link", headers={**signed, "Authorization": "Bearer invalid"}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/account-link", headers={**signed, "Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/account-link", headers=signed).json()["state"] == "LINKED"
        assert client.post("/api/session", headers=signed).status_code == 200
        google = {"Authorization": "Bearer valid-google", "Origin": "https://navmate.example"}
        response = client.get("/api/navmate-migration/status", headers=google)
        assert response.headers["Access-Control-Allow-Origin"] == "https://navmate.example"
        assert response.json()["state"] == "LINKED"
        assert (
            client.get(
                "/api/navmate-migration/status",
                headers={**google, "Origin": "https://evil.example"},
            ).status_code
            == 403
        )
        assert client.get("/api/navmate-migration/status").status_code == 401
        service.begin(KEY)
        assert client.post("/api/session", headers=signed).status_code == 423
        service.step(KEY, Remote())
        response = client.get(
            "/", headers={**signed, "Accept": "text/html"}, follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "https://navmate.example/"
        assert (
            client.get(
                "/",
                headers={"Accept": "text/html", "Cf-Access-Jwt-Assertion": "invalid"},
                follow_redirects=False,
            ).status_code
            != 303
        )


def test_latest_device_mismatch_is_not_silently_accepted(migration):
    value = project(migration, saved=False)
    migration.link(OWNER, GOOGLE)
    remote = Remote()
    migration.begin(KEY)
    migration.step(KEY, remote)
    with migration.lock(KEY):
        migration.fail(KEY)
    remote.rows[str(value.id)]["latestDeviceId"] = "other-device"
    before = copy.deepcopy(remote.rows)
    with pytest.raises(WebApplicationError):
        finish(migration, remote)
    assert remote.rows == before
    assert migration.status(KEY)["state"] == "LINKED"


def test_last_calculation_is_portable_and_matching_only(migration):
    # Reuse real Legacy route/planning fixture and calculation, rather than fabricated outputs.
    from test_issue_94_facade_persistence import _new_project

    session, _ = _new_project(migration.web)
    migration.web.calculate(session)
    assert session.last_calculation is not None
    source = session.last_calculation.model_dump(mode="json")
    migration.link(OWNER, GOOGLE)
    snapshot = migration.snapshot(KEY)
    assert len(snapshot) == 1
    last = json.loads(snapshot[0]["payload"])["lastCalculation"]
    assert last is not None
    assert last["outcome"] == source["outcome"]
    assert last["calculation_fingerprint"] == source["calculation_fingerprint"]
    assert "web_owner_id" not in last["project"]["metadata"]
    session.project.total_usable_fuel_gal -= 5
    migration.web.project_service.repository.autosave(session.project)
    stale = migration.snapshot(KEY)
    assert json.loads(stale[0]["payload"])["lastCalculation"] is None


def test_concurrent_two_owner_link_has_one_winner_and_domain_conflict(migration):
    from threading import Barrier

    barrier = Barrier(2)

    def link(owner):
        barrier.wait(timeout=5)
        try:
            return migration.link(owner, GOOGLE)["state"]
        except WebApplicationError as error:
            return error.code

    with ThreadPoolExecutor(2) as executor:
        futures = [executor.submit(link, owner) for owner in (OWNER, "other@example.com")]
        assert sorted(future.result() for future in futures) == ["ACCOUNT_ALREADY_LINKED", "LINKED"]
    with migration.db() as db:
        assert db.execute("SELECT count(*) FROM links").fetchone()[0] == 1
