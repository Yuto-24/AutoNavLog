"""Rescue integrity, isolation and refusal paths using real Legacy storage."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from autonavlog.storage.local import LocalProjectRepository
from autonavlog.storage.repository import owner_storage_key
from scripts import legacy_rescue as rescue


@pytest.fixture
def source(tmp_path, project):
    root = tmp_path / "source"
    repository = LocalProjectRepository(root)
    for identity in ("owner-a", "owner-b"):
        value = project.model_copy(deep=True)
        value.id = uuid4()
        value.metadata["web_owner_id"] = identity
        value = repository.save(value, 0).project
        repository.autosave(value, set_last_opened=True)
    with sqlite3.connect(root / "account-links.sqlite3") as db:
        for sql in rescue.SCHEMA:
            db.execute(sql)
        for identity in ("owner-a", "owner-b"):
            db.execute("INSERT INTO links(owner,account,subject,state) VALUES(?,?,?,'LINKED')",
                       (owner_storage_key(identity), "account-" + identity, "subject-" + identity))
    (root / "credentials.json").write_text("excluded-secret")
    (root / "weather").mkdir()
    (root / "weather/cache").write_text("excluded-cache")
    return root


def make_snapshot(source, destination, **kwargs):
    now = datetime.now(UTC)
    return rescue.snapshot(source, destination, **{
        "ended_at": (now - timedelta(hours=1)).isoformat(),
        "retain_until": (now + timedelta(days=30)).isoformat(),
        "revision": "a" * 40, "quiescence": "synthetic offline fixture", **kwargs})


def test_snapshot_preserves_bytes_and_owner_extraction_loads_in_repository(source, tmp_path):
    before = rescue.fingerprint(rescue.files(source))
    bundle = tmp_path / "bundle"
    result = make_snapshot(source, bundle)
    assert result["kind"] == "full"
    assert rescue.fingerprint(rescue.files(source)) == before
    assert not (bundle / "data/credentials.json").exists()
    assert not (bundle / "data/weather").exists()
    assert (bundle.stat().st_mode & 0o777) == 0o700
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in rescue.files(bundle).values())
    output = tmp_path / "owner"
    key = owner_storage_key("owner-a")
    owner = rescue.extract_owner(bundle, output, key,
                                expected_digest=hashlib.sha256((bundle / "manifest.json")
                                                               .read_bytes()).hexdigest())
    assert owner["kind"] == "owner"
    assert not (output / "data/projects/index.json").exists()
    # An actual repository reconstructs the index in the disposable owner copy only.
    repository = LocalProjectRepository(output / "data")
    summaries = repository.list_projects()
    assert len(summaries) == 1
    assert summaries[0].web_owner_id == "owner-a"
    assert repository.load(summaries[0].id).metadata["web_owner_id"] == "owner-a"
    assert repository.load_owner_project(key) == summaries[0].id
    for path in rescue.files(output).values():
        assert b"owner-b" not in path.read_bytes()
    with sqlite3.connect(output / "data/account-links.sqlite3") as db:
        assert db.execute("SELECT owner FROM links").fetchall() == [(key,)]
    assert rescue.verify(bundle) == result


@pytest.mark.parametrize("change", ["edit", "extra", "missing", "symlink", "hardlink"])
def test_verify_rejects_changed_bundle(source, tmp_path, change):
    bundle = tmp_path / "bundle"
    make_snapshot(source, bundle)
    path = bundle / "data/projects/index.json"
    if change == "edit":
        path.write_text("{}")
    elif change == "extra":
        (bundle / "extra").write_text("unexpected")
    elif change == "missing":
        path.unlink()
    elif change == "symlink":
        path.unlink()
        path.symlink_to(source / "projects/index.json")
    else:
        (tmp_path / "linked").hardlink_to(path)
    with pytest.raises(ValueError):
        rescue.verify(bundle)


@pytest.mark.parametrize("name", ["projects", "owners", "account-links.sqlite3"])
def test_source_symlink_rejected(source, tmp_path, name):
    actual = source / name
    moved = tmp_path / name
    actual.rename(moved)
    actual.symlink_to(moved)
    with pytest.raises(ValueError):
        make_snapshot(source, tmp_path / "bundle")


def test_existing_destination_and_nested_destination_are_refused(source, tmp_path):
    with pytest.raises(ValueError):
        make_snapshot(source, source / "backup")
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "important").write_text("preserve")
    with pytest.raises(FileExistsError):
        make_snapshot(source, destination)
    assert (destination / "important").read_text() == "preserve"


@pytest.mark.parametrize("override", [
    {"ended_at": "2100-01-01T00:00:00+00:00"},
    {"retain_until": "2000-01-01T00:00:00+00:00"},
    {"retain_until": "2100-01-01"}, {"revision": "main"}, {"quiescence": ""},
])
def test_required_policy_is_not_defaulted(source, tmp_path, override):
    with pytest.raises(ValueError):
        make_snapshot(source, tmp_path / "bundle", **override)
    assert not (tmp_path / "bundle").exists()


def test_active_migration_blocks_snapshot_without_expiring_or_editing_lease(source, tmp_path):
    with sqlite3.connect(source / "account-links.sqlite3") as db:
        db.execute("UPDATE links SET state='MIGRATING',expires=0")
    before = rescue.fingerprint(rescue.files(source))
    with pytest.raises(ValueError, match="migration"):
        make_snapshot(source, tmp_path / "bundle")
    assert rescue.fingerprint(rescue.files(source)) == before


def test_committed_wal_is_in_snapshot(source, tmp_path):
    with sqlite3.connect(source / "account-links.sqlite3") as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("UPDATE links SET completed=17")
        db.commit()
        bundle = tmp_path / "bundle"
        make_snapshot(source, bundle)
        with sqlite3.connect(bundle / "data/account-links.sqlite3") as copied:
            assert copied.execute("SELECT DISTINCT completed FROM links").fetchall() == [(17,)]
        assert not (bundle / "data/account-links.sqlite3-wal").exists()


def test_changed_source_has_no_completed_manifest(source, tmp_path, monkeypatch):
    original = rescue.write

    def change(path, value):
        original(path, value)
        if path.name == "index.json":
            (source / "projects/index.json").write_text("changed")

    monkeypatch.setattr(rescue, "write", change)
    with pytest.raises(ValueError, match="Source changed"):
        make_snapshot(source, tmp_path / "bundle")
    assert not (tmp_path / "bundle/manifest.json").exists()


@pytest.mark.parametrize("corruption", ["foreign-checkpoint", "foreign-state", "bad-json"])
def test_ambiguous_ownership_never_produces_owner_bundle(source, tmp_path, corruption):
    first = next(p for p in (source / "projects").glob("*/project.json")
                 if json.loads(p.read_bytes())["metadata"]["web_owner_id"] == "owner-a")
    if corruption == "foreign-checkpoint":
        value = json.loads(first.read_bytes())
        value["metadata"]["web_owner_id"] = "owner-b"
        first.write_text(json.dumps(value))
    elif corruption == "foreign-state":
        path = source / "owners" / owner_storage_key("owner-a") / "state.json"
        state = json.loads(path.read_bytes())
        state["last_opened_project_id"] = str(uuid4())
        path.write_text(json.dumps(state))
    else:
        first.write_text("private corrupt contents")
    # The full archive preserves damaged data for manual investigation without guessing its owner.
    bundle = tmp_path / "bundle"
    make_snapshot(source, bundle)
    with pytest.raises(ValueError):
        rescue.extract_owner(bundle, tmp_path / "owner", owner_storage_key("owner-a"),
                             expected_digest=hashlib.sha256((bundle / "manifest.json")
                                                            .read_bytes()).hexdigest())
    assert not (tmp_path / "owner").exists()


def test_cli_redacts_private_parse_error(source, tmp_path):
    bundle = tmp_path / "bundle"
    make_snapshot(source, bundle)
    (bundle / "manifest.json").write_text("private owner@example.com")
    result = subprocess.run([sys.executable, str(Path(rescue.__file__)), "verify", "--bundle",
                             str(bundle), "--expected-manifest-sha256", "a" * 64],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"status": "BLOCKED", "retirement_authorized": False}
    assert result.stderr == ""


def test_separate_manifest_digest_rejects_rewritten_retention(source, tmp_path):
    bundle = tmp_path / "bundle"
    make_snapshot(source, bundle)
    path = bundle / "manifest.json"
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    value = json.loads(path.read_bytes())
    value["retain_until"] = "2100-01-01T00:00:00+00:00"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="separately retained"):
        rescue.extract_owner(bundle, tmp_path / "owner", owner_storage_key("owner-a"),
                             expected_digest=expected)


def test_expired_snapshot_can_be_verified_but_not_used_for_recovery(source, tmp_path):
    bundle = tmp_path / "bundle"
    make_snapshot(source, bundle)
    path = bundle / "manifest.json"
    value = json.loads(path.read_bytes())
    value.update(migration_ended_at="2000-01-01T00:00:00+00:00",
                 created_at="2000-01-02T00:00:00+00:00", retain_until="2000-01-03T00:00:00+00:00")
    path.write_text(json.dumps(value))
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    rescue.verify(bundle, expected_digest=expected)
    with pytest.raises(ValueError, match="Retention expired"):
        rescue.extract_owner(bundle, tmp_path / "owner", owner_storage_key("owner-a"),
                             expected_digest=expected)


@pytest.mark.parametrize("sql", [
    "CREATE TABLE unknown (secret TEXT)",
    "INSERT INTO receipts VALUES ('missing-owner','id',NULL,'{}')",
    "UPDATE links SET state='UNKNOWN'",
])
def test_incompatible_or_orphan_registry_is_not_certified(source, tmp_path, sql):
    with sqlite3.connect(source / "account-links.sqlite3") as db:
        db.execute(sql)
    with pytest.raises(ValueError):
        make_snapshot(source, tmp_path / "bundle")
    assert not (tmp_path / "bundle/manifest.json").exists()


@pytest.mark.parametrize("raw", [b'{"id":1,"id":2}', b'{"id":NaN}', b'{"id":1e999}'])
def test_ambiguous_json_refused(tmp_path, raw):
    path = tmp_path / "value.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        rescue.read_json(path)
