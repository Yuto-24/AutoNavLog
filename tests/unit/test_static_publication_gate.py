"""Optional missing evidence cannot waive a real static publication violation."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jma_gpv_weather.errors import SelectedRunCoverageError

from scripts.static_ops import publication_gate as gate
from scripts.static_ops.refresh_feed import refresh
from scripts.static_ops.validate_feed import validate_feed


@pytest.fixture
def approved():
    env = {
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "STATIC_RUNNER_CLASS": "ubuntu-latest",
        "GITHUB_REPOSITORY": "Yuto-24/AutoNavLog", "APPROVED_SHA": "a" * 40,
        "STATIC_ORIGIN": gate.ORIGIN, "CLOUDFLARE_ACCOUNT_ID": "c" * 32,
        **{name: "public-" + name for name in gate.CONFIGURATION},
    }
    canonical = {"commit": env["APPROVED_SHA"], "dirty": False,
                 "configuration": {name: env[name] for name in gate.CONFIGURATION}}
    event = {"repository": {"private": False, "full_name": env["GITHUB_REPOSITORY"]}}
    return env, event, canonical


def test_missing_all_optional_provider_credentials_or_reports_does_not_block_static(approved):
    env, event, canonical = approved
    assert not any("TOKEN" in name or "REPORT" in name for name in env)
    assert gate.identity(env, event, canonical) == "a" * 40
    # Old reports and optional settings are not consumed as evidence either.
    env.update(STATIC_QUOTA_REPORT="expired", STATIC_GOOGLE_ACCESS_TOKEN="unavailable")
    assert gate.identity(env, event, canonical) == "a" * 40


@pytest.mark.parametrize("patch", [
    {"REQUESTED_SHA": "b" * 40}, {"APPROVED_SHA": "main"},
    {"STATIC_ORIGIN": "https://navmate.pages.dev"}, {"CLOUDFLARE_ACCOUNT_ID": ""},
    {"RUNNER_ENVIRONMENT": "self-hosted"}, {"STATIC_RUNNER_CLASS": "ubuntu-large"},
    {"RUNNER_OS": "Windows"}, {"RUNNER_ARCH": "ARM64"}, {"GITHUB_ACTIONS": "false"},
    {"VITE_TAF_PROXY_URL": ""}, {"VITE_FIREBASE_PROJECT_ID": "changed"},
])
def test_wrong_sha_origin_runner_or_public_configuration_stops_static(approved, patch):
    env, event, canonical = approved
    env.update(patch)
    with pytest.raises(ValueError):
        gate.identity(env, event, canonical)


def test_private_repo_and_live_rollback_fail_closed(approved):
    env, event, canonical = approved
    event["repository"]["private"] = True
    with pytest.raises(ValueError, match="public"):
        gate.identity(env, event, canonical)
    event["repository"]["private"] = False
    canonical["commit"] = "b" * 40
    with pytest.raises(ValueError, match="Canonical"):
        gate.identity(env, event, canonical)


@pytest.mark.parametrize("name", ["functions/index.js", "web/functions/taf.js", "web/_worker.js",
                                  "wrangler.toml", "web/.wrangler/deploy/config.json"])
def test_runtime_outside_dist_is_forbidden(tmp_path, name):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    with pytest.raises(ValueError):
        gate.static_sources(tmp_path)


@pytest.mark.parametrize("extra", [{"kv_namespaces": []}, {"env": {}}, {"vars": {}},
                                  {"main": "worker.js"}, {"services": []}, {"r2_buckets": []}])
def test_even_inactive_candidate_runtime_configuration_is_rejected(tmp_path, extra):
    (tmp_path / "web").mkdir()
    path = tmp_path / "web/wrangler.jsonc"
    path.write_text(json.dumps({**gate.UPLOAD_CONFIG, **extra}))
    with pytest.raises(ValueError, match="configuration"):
        gate.static_sources(tmp_path)


def fixture_artifact(app, canonical):
    dist = app / "web/dist-static"
    dist.mkdir(parents=True)
    files = {"index.html": b"static", "_headers": b"/*\n Cache-Control: no-cache",
             "404.html": b"missing", "local/core.whl": b"wheel", "local/data.zip": b"data"}
    manifest = {"version": "1.0.0", "pyodideVersion": "0.27.7", "wheels": ["core.whl"],
                "data": "data.zip", "sha256": {name.split("/")[1]: hashlib.sha256(value).hexdigest()
                                                 for name, value in files.items()
                                                 if name.startswith("local/")}}
    files["local/manifest.json"] = json.dumps(manifest).encode()
    payload = b"artifact integrity fixture; real NPZ validation is tested separately"
    digest = hashlib.sha256(payload).hexdigest()
    files[f"weather/msm/{digest}.npz"] = payload
    files["weather/msm/catalog.json"] = json.dumps({
        "expires_at": (datetime.now(UTC) + timedelta(hours=5)).isoformat(),
        "assets": [{"file": digest + ".npz", "bytes": len(payload), "sha256": digest}],
    }).encode()
    for name, value in files.items():
        path = dist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    candidate = {**canonical, "version": "1.0.0", "pyodideVersion": "0.27.7", "files": {
        name: {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in sorted(files.items())}}
    (dist / "release.json").write_text(json.dumps(candidate))
    return dist


def test_trusted_checker_cannot_be_replaced_by_candidate_and_upload_context_is_minimal(
    tmp_path, approved
):
    _, _, canonical = approved
    app = tmp_path / "app"
    dist = fixture_artifact(app, canonical)
    scripts = app / "web/scripts"
    scripts.mkdir()
    (scripts / "check-static.mjs").write_text("process.exit(0)")
    gate.artifact(app, canonical)
    gate.upload_context(app, tmp_path / "upload", canonical)
    assert json.loads((tmp_path / "upload/wrangler.json").read_text()) == gate.UPLOAD_CONFIG
    assert set(p.name for p in (tmp_path / "upload").iterdir()) == {"dist-static", "wrangler.json"}
    (dist / "index.html").write_text("tamper")
    with pytest.raises(ValueError, match="inventory"):
        gate.artifact(app, canonical)
    (tmp_path / "upload/functions").mkdir()
    with pytest.raises(ValueError, match="working-directory"):
        gate.verify_context(tmp_path / "upload")


def test_static_artifact_expiry_and_source_mutation_stop_build_only(tmp_path, approved):
    _, _, canonical = approved
    dist = fixture_artifact(tmp_path, canonical)
    candidate = json.loads((dist / "release.json").read_text())
    candidate["configuration"]["VITE_TAF_PROXY_URL"] = "disabled"
    (dist / "release.json").write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="configuration"):
        gate.artifact(tmp_path, canonical)


@pytest.mark.parametrize("name, payload", [
    ("_worker.js", "export default {}"), ("functions/index.js", "export default {}"),
    (".env", "TOKEN=private"), ("credentials.json", '{"client_secret":"private"}'),
    ("private.txt", "-----BEGIN PRIVATE KEY-----"),
])
def test_sealed_worker_and_secret_material_are_still_rejected(tmp_path, approved, name, payload):
    _, _, canonical = approved
    dist = fixture_artifact(tmp_path, canonical)
    path = dist / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)
    candidate = json.loads((dist / "release.json").read_text())
    candidate["files"][name] = {"bytes": path.stat().st_size,
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (dist / "release.json").write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="Runtime/private|Secret"):
        gate.artifact(tmp_path, canonical)


def test_sealed_expired_catalog_stops_static_build_only(tmp_path, approved):
    _, _, canonical = approved
    dist = fixture_artifact(tmp_path, canonical)
    path = dist / "weather/msm/catalog.json"
    catalog = json.loads(path.read_text())
    catalog["expires_at"] = "2000-01-01T00:00:00Z"
    path.write_text(json.dumps(catalog))
    candidate = json.loads((dist / "release.json").read_text())
    candidate["files"]["weather/msm/catalog.json"] = {
        "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (dist / "release.json").write_text(json.dumps(candidate))
    with pytest.raises(ValueError, match="expired"):
        gate.artifact(tmp_path, canonical)


def test_real_payload_hash_run_coverage_freshness_and_retained_only(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/msm-portable"
    catalog = json.loads((fixture / "catalog.json").read_text())
    now = datetime(2026, 9, 16, 3, tzinfo=UTC)
    catalog.update(generated_at=now.isoformat(), expires_at=(now + timedelta(hours=6)).isoformat())
    for asset in catalog["assets"]:
        (tmp_path / asset["file"]).write_bytes((fixture / asset["file"]).read_bytes())
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    report = {"runs": [{"run": a["run"]} for a in catalog["assets"]]}
    assert validate_feed(tmp_path, report, now, horizon_hours=3)["assets"] == 2
    with pytest.raises(ValueError, match="Runs required"):
        validate_feed(tmp_path, {"runs": []}, now, horizon_hours=3)
    with pytest.raises(SelectedRunCoverageError):
        validate_feed(tmp_path, report, now)  # Fixed fixture cannot cover 24 hours.
    with pytest.raises(ValueError, match="freshness"):
        validate_feed(tmp_path, report, now + timedelta(hours=7), horizon_hours=3)
    first = catalog["assets"][0]
    (tmp_path / first["file"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="byte length"):
        validate_feed(tmp_path, report, now, horizon_hours=3)


def test_renewed_timestamp_with_same_prepared_run_does_not_pass(monkeypatch, tmp_path):
    (tmp_path / "catalog.json").write_text(json.dumps({"assets": [{"run": "20261007090000"}]}))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        [], 0, stdout=json.dumps({"runs": [{"run": "20261007090000"}]})))
    with pytest.raises(ValueError, match="No newer real"):
        refresh(tmp_path / "producer", tmp_path, tmp_path / "cache")


def test_pages_upload_requires_existing_correct_static_project():
    project = {"name": "navmate", "production_branch": "main",
               "domains": ["navmate.pages.dev", "navmate.yuto24.com"],
               "deployment_configs": {"production": {"kv_namespaces": {}, "env_vars": {}}}}
    gate.verify_project(project)
    for key, value in [("name", "other"), ("production_branch", "preview"), ("domains", []),
                       ("deployment_configs", {})]:
        with pytest.raises(ValueError):
            gate.verify_project({**project, key: value})
    project["deployment_configs"]["production"]["services"] = [{"service": "paid-worker"}]
    with pytest.raises(ValueError, match="binding"):
        gate.verify_project(project)


@pytest.mark.parametrize("name, kind", [
    ("VITE_TAF_PROXY_URL", "plain_text"), ("OTHER_SETTING", "plain_text"),
    ("PRIVATE_SETTING", "secret_text"),
])
def test_existing_pages_env_is_allowed_without_disclosing_or_mutating_it(name, kind, capsys):
    project = {"name": "navmate", "production_branch": "main",
               "domains": ["navmate.yuto24.com"], "deployment_configs": {"production": {
                   "env_vars": {name: {"type": kind, "value": "PRIVATE_CANARY"}},
               }}}
    before = json.dumps(project)
    gate.verify_project(project)
    assert json.dumps(project) == before
    assert capsys.readouterr() == ("", "")
    # The env exception must not bypass target or other runtime-binding checks.
    for binding in ("kv_namespaces", "durable_object_namespaces", "d1_databases", "r2_buckets",
                    "services", "queue_producers", "analytics_engine_datasets", "ai_bindings",
                    "vectorize_bindings", "hyperdrive_bindings"):
        runtime = {**project["deployment_configs"]["production"], binding: {"id": "resource"}}
        with pytest.raises(ValueError, match="binding"):
            gate.verify_project({**project, "deployment_configs": {"production": runtime}})
    for key, value in [("name", "other"), ("production_branch", "preview"), ("domains", []),
                       ("deployment_configs", {}), ("deployment_configs", {"production": None})]:
        with pytest.raises(ValueError):
            gate.verify_project({**project, key: value})
    assert capsys.readouterr() == ("", "")
