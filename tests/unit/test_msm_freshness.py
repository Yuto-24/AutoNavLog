"""Hints cannot replace canonical integrity checks and migrate without app source updates."""
import hashlib
import json

import pytest

from scripts.static_ops import freshness as gate

COMMIT = "b" * 40


def inventory(raw):
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def deployment(tmp_path):
    payload = b"fixture forecast bytes"
    digest = hashlib.sha256(payload).hexdigest()
    catalog = {"schema_version": 1, "generated_at": "2026-10-25T06:00:00Z",
               "expires_at": "2026-10-25T12:00:00Z", "assets": [
                   {"run": "20261025030000", "sha256": digest,
                    "file": digest + ".npz", "bytes": len(payload)}]}
    directory = tmp_path / "weather/msm"
    directory.mkdir(parents=True)
    raw = json.dumps(catalog).encode()
    (directory / "catalog.json").write_bytes(raw)
    (directory / (digest + ".npz")).write_bytes(payload)
    release = {"commit": COMMIT, "dirty": False, "files": {
        "weather/msm/catalog.json": inventory(raw),
        "weather/msm/" + digest + ".npz": inventory(payload)}}
    (tmp_path / "release.json").write_text(json.dumps(release))
    return tmp_path


def fetcher(dist):
    def fetch(url, limit):
        raw = (dist / url.removeprefix("https://canonical.test/")).read_bytes()
        if len(raw) > limit:
            raise ValueError("Body limit")
        return raw
    return fetch


def test_legacy_can_seed_migration_and_new_hint_is_in_same_inventory(deployment):
    gate.canonical("https://canonical.test", COMMIT, fetcher(deployment))
    before = (deployment / "weather/msm/catalog.json").read_bytes()
    gate.write(deployment)
    raw = (deployment / gate.NAME).read_bytes()
    assert len(raw) < gate.LIMIT
    assert json.loads(raw)["run"] == "20261025030000"
    assert (deployment / "weather/msm/catalog.json").read_bytes() == before
    gate.canonical("https://canonical.test", COMMIT, fetcher(deployment))
    gate.write(deployment)  # deterministic; never renews generation/expiry
    assert (deployment / gate.NAME).read_bytes() == raw


@pytest.mark.parametrize("mutation", [
    "run", "commit", "expiry", "catalog", "payload", "missing", "oversize"])
def test_canonical_mismatch_failure_or_missing_recorded_hint_blocks(deployment, mutation):
    gate.write(deployment)
    hint_path = deployment / gate.NAME
    hint = json.loads(hint_path.read_bytes())
    if mutation in {"run", "commit", "expiry"}:
        hint[{"run": "run", "commit": "app_commit", "expiry": "expires_at"}[mutation]] = "bad"
        hint_path.write_text(json.dumps(hint))
    elif mutation == "catalog":
        (deployment / "weather/msm/catalog.json").write_bytes(b"{}")
    elif mutation == "payload":
        next((deployment / "weather/msm").glob("*.npz")).write_bytes(b"corrupt")
    elif mutation == "missing":
        hint_path.unlink()
    else:
        hint_path.write_bytes(b"x" * (gate.LIMIT + 1))
    with pytest.raises((ValueError, FileNotFoundError)):
        gate.canonical("https://canonical.test", COMMIT, fetcher(deployment))


def test_inventory_failure_and_transport_errors_never_become_success(deployment):
    with pytest.raises(ValueError, match="identity"):
        gate.canonical("https://canonical.test", "c" * 40, fetcher(deployment))
    with pytest.raises(TimeoutError):
        gate.canonical("https://canonical.test", COMMIT,
                       lambda *_: (_ for _ in ()).throw(TimeoutError()))
