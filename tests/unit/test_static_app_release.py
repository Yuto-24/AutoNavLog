"""App publication boundary: real inventory gates, simulated provider side effects."""

import copy
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.static_ops import app_release as app
from scripts.static_ops import publication_gate as gate
from tests.unit.test_static_publication_gate import fixture_artifact

OLD, NEW = "a" * 40, "b" * 40


class SimulatedOperator(app.Operator):
    def __init__(self, root, *, automation="true"):
        super().__init__(root)
        self.state = {
            "variables": {
                **{name: "public-" + name for name in gate.CONFIGURATION},
                "STATIC_APPROVED_SHA": OLD,
                "STATIC_AUTOMATION_ENABLED": automation,
                "STATIC_ORIGIN": gate.ORIGIN,
                "CLOUDFLARE_ACCOUNT_ID": "c" * 32,
            },
            "target": {"id": "old-deployment", "commit": OLD, "project_digest": "p"},
            "rules_digest": "r",
            "tooling_sha": "d" * 40,
            "version": "1.0.0",
        }
        self.workflow_state = "active"
        self.calls = []
        self.fail = None
        self.uploaded = None
        self.busy = False
        self.snapshot_hook = None

    def event(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise app.Blocked("Simulated failure")

    def snapshot(self, sha, current, *, paused=False):
        self.event("paused-snapshot" if paused else "snapshot")
        if self.snapshot_hook:
            self.snapshot_hook(self, paused)
        assert self.state["variables"]["STATIC_APPROVED_SHA"] == current
        assert self.workflow_state == ("disabled_manually" if paused else "active")
        self.idle()
        return copy.deepcopy(self.state)

    def build(self, sha, snapshot, destination):
        self.event("fresh-build" if destination.name == "fresh" else "prepare-build")
        destination.mkdir()
        config = {key: snapshot["variables"][key] for key in gate.CONFIGURATION}
        fixture_artifact(
            destination / "app", {"commit": sha, "dirty": False, "configuration": config}
        )
        # Make fresh and prepared inventory distinguishable without invalid hashes.
        if destination.name == "fresh":
            dist = destination / "app/web/dist-static"
            (dist / "index.html").write_text("fresh")
            release = json.loads((dist / "release.json").read_text())
            release["files"]["index.html"] = {"bytes": 5, "sha256": app.digest(b"fresh")}
            app.write_json(dist / "release.json", release)
        gate.upload_context(
            destination / "app", destination / "upload", {"commit": sha, "configuration": config}
        )
        app.write_json(
            destination / "feed-report.json", {"runs": [{"run": "real-library-tested-separately"}]}
        )
        return self.validate_build(destination, sha, snapshot)

    def validate_build(self, destination, sha, snapshot):
        self.event("validate-" + destination.name)
        gate.verify_context(destination / "upload")
        candidate = json.loads((destination / "upload/dist-static/release.json").read_text())
        app.require(
            candidate["commit"] == sha
            and candidate["configuration"]
            == {key: snapshot["variables"][key] for key in gate.CONFIGURATION},
            "Wrong candidate",
        )
        return candidate

    def variables(self):
        self.event("variables")
        return copy.deepcopy(self.state["variables"])

    def workflow(self):
        self.event("workflow")
        return {"state": self.workflow_state}

    def idle(self):
        self.event("idle")
        app.require(not self.busy, "Existing workflow not idle")

    def compatibility(self, *args):
        self.event("rules")
        return self.state["rules_digest"]

    def set_variable(self, name, value):
        action = (
            "approve"
            if name == "STATIC_APPROVED_SHA"
            else "pause"
            if value == "false"
            else "resume"
        )
        self.event(action)
        self.state["variables"][name] = value

    def set_workflow(self, enabled):
        self.event("enable" if enabled else "disable")
        self.workflow_state = "active" if enabled else "disabled_manually"

    def upload(self, destination, sha, snapshot):
        self.event("upload")
        self.uploaded = destination
        self.state["target"]["commit"] = sha
        self.state["target"]["id"] = "new-deployment"

    def verify(self, destination, sha, snapshot):
        self.event("verify")
        assert self.uploaded == destination and self.state["target"]["commit"] == sha


@pytest.fixture
def prepared(tmp_path):
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    operator = SimulatedOperator(tmp_path / "tooling")
    journal = app.Journal(store / "prepare-status.json", "prepare", NEW)
    app.prepare(operator, NEW, OLD, store, journal)
    reviewed = app.digest((store / "plan.json").read_bytes())
    operator.calls.clear()
    return operator, store, reviewed


def run_publish(prepared):
    operator, store, reviewed = prepared
    journal = app.Journal(store / "publish-status.json", "publish", NEW)
    try:
        app.publish(operator, NEW, OLD, store, reviewed, journal)
    except Exception:
        journal.finish(False)
        raise
    journal.finish(True)
    return journal.value


def test_prepare_has_no_provider_writes_and_keeps_requested_sha(prepared):
    operator, store, _ = prepared
    plan = json.loads((store / "plan.json").read_text())
    assert plan["sha"] == NEW and plan["current_sha"] == OLD
    assert operator.uploaded is None
    assert operator.state["variables"]["STATIC_APPROVED_SHA"] == OLD
    assert operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "true"
    assert operator.workflow_state == "active"


def test_success_uploads_fresh_inventory_then_approves_and_resumes(prepared):
    operator, store, _ = prepared
    value = run_publish(prepared)
    assert operator.uploaded == store / "fresh"
    assert app.digest((store / "fresh/upload/dist-static/release.json").read_bytes()) != app.digest(
        (store / "prepared/upload/dist-static/release.json").read_bytes()
    )
    assert operator.calls.index("pause") < operator.calls.index("fresh-build")
    assert (
        operator.calls.index("verify")
        < operator.calls.index("approve")
        < operator.calls.index("resume")
    )
    assert value["status"] == "OK" and value["previous_deployment"] == "old-deployment"
    assert value["final_approved_sha"] == NEW
    assert operator.workflow_state == "active"


def test_disabled_automation_is_preserved(prepared):
    operator, store, _ = prepared
    plan = json.loads((store / "plan.json").read_text())
    plan["snapshot"]["variables"]["STATIC_AUTOMATION_ENABLED"] = "false"
    operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] = "false"
    app.write_json(store / "plan.json", plan)
    run_publish((operator, store, app.digest((store / "plan.json").read_bytes())))
    assert "resume" not in operator.calls
    assert operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "false"


@pytest.mark.parametrize(
    "failure,production,approved",
    [
        ("snapshot", "UNCHANGED", "UNCHANGED"),
        ("validate-prepared", "UNCHANGED", "UNCHANGED"),
        ("pause", "UNCHANGED", "UNCHANGED"),
        ("disable", "UNCHANGED", "UNCHANGED"),
        ("paused-snapshot", "UNCHANGED", "UNCHANGED"),
        ("fresh-build", "UNCHANGED", "UNCHANGED"),
        ("upload", "UNKNOWN", "UNCHANGED"),
        ("verify", "CONFIRMED", "UNCHANGED"),
        ("variables", "CONFIRMED", "UNCHANGED"),
        ("rules", "CONFIRMED", "UNCHANGED"),
        ("approve", "CONFIRMED", "UNKNOWN"),
        ("enable", "CONFIRMED", "CONFIRMED"),
        ("resume", "CONFIRMED", "CONFIRMED"),
    ],
)
def test_failure_reports_partial_state_and_never_unconditionally_resumes(
    prepared, failure, production, approved
):
    operator, store, _ = prepared
    operator.fail = failure
    with pytest.raises(app.Blocked):
        run_publish(prepared)
    status = json.loads((store / "publish-status.json").read_text())
    assert status["changes"]["production"] == production
    assert status["changes"]["approved_sha"] == approved
    assert status["status"] == "BLOCKED"
    assert "rollback" not in operator.calls
    if failure != "resume":
        assert "resume" not in operator.calls
    if failure not in ("snapshot", "validate-prepared", "pause"):
        assert operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "false"


@pytest.mark.parametrize(
    "field",
    [
        "STATIC_ORIGIN",
        "VITE_FIREBASE_PROJECT_ID",
        "VITE_TAF_PROXY_URL",
        "VITE_LEGACY_MIGRATION_URL",
        "CLOUDFLARE_ACCOUNT_ID",
    ],
)
def test_configuration_drift_before_pause_blocks_all_mutation(prepared, field):
    operator, _, _ = prepared
    operator.state["variables"][field] = "changed"
    with pytest.raises(app.Blocked, match="changed"):
        run_publish(prepared)
    assert "pause" not in operator.calls and operator.uploaded is None


@pytest.mark.parametrize("change", ["rules_digest", "tooling_sha", "version"])
def test_review_identity_drift_is_rejected(prepared, change):
    operator, _, _ = prepared
    operator.state[change] = "changed"
    with pytest.raises(app.Blocked):
        run_publish(prepared)
    assert "pause" not in operator.calls


def test_same_source_scheduled_feed_can_advance_before_pause(prepared):
    operator, _, _ = prepared
    operator.state["target"]["id"] = "scheduled-feed-deployment"
    run_publish(prepared)


def test_remote_deployment_during_fresh_build_keeps_schedule_paused(prepared):
    operator, _, _ = prepared
    original = operator.build

    def build(*args):
        candidate = original(*args)
        operator.state["target"]["id"] = "competing-deployment"
        return candidate

    operator.build = build
    with pytest.raises(app.Blocked):
        run_publish(prepared)
    assert operator.uploaded is None
    assert operator.workflow_state == "disabled_manually"


def test_queued_job_after_pause_is_not_canceled_or_uploaded(prepared):
    operator, _, _ = prepared
    operator.snapshot_hook = lambda op, paused: setattr(op, "busy", paused)
    with pytest.raises(app.Blocked):
        run_publish(prepared)
    assert operator.uploaded is None and "resume" not in operator.calls


@pytest.mark.parametrize("tamper", ["plan", "release", "feed", "asset", "stale", "future", "sha"])
def test_unreviewed_old_or_tampered_evidence_has_no_provider_writes(prepared, tamper):
    operator, store, reviewed = prepared
    if tamper == "plan":
        with (store / "plan.json").open("a") as stream:
            stream.write(" ")
    elif tamper in ("stale", "future", "sha"):
        plan = json.loads((store / "plan.json").read_text())
        if tamper == "sha":
            plan["sha"] = "e" * 40
        else:
            plan["prepared_at"] = (
                datetime.now(UTC) + timedelta(minutes=-31 if tamper == "stale" else 5)
            ).isoformat()
        app.write_json(store / "plan.json", plan)
        reviewed = app.digest((store / "plan.json").read_bytes())
    else:
        path = {
            "release": store / "prepared/upload/dist-static/release.json",
            "feed": store / "prepared/feed-report.json",
            "asset": store / "prepared/upload/dist-static/index.html",
        }[tamper]
        path.write_text("tamper")
    with pytest.raises((app.Blocked, ValueError)):
        run_publish((operator, store, reviewed))
    assert "pause" not in operator.calls


def test_publish_requires_explicit_review_digest(prepared):
    operator, store, _ = prepared
    with pytest.raises(app.Blocked, match="reviewed"):
        run_publish((operator, store, ""))
    assert not operator.calls


def test_timeout_after_upload_remains_unknown_and_preserves_new_remote_state(prepared):
    operator, store, _ = prepared
    original = operator.upload

    def upload(*args):
        original(*args)
        raise TimeoutError("SECRET_CANARY provider response")

    operator.upload = upload
    with pytest.raises(TimeoutError):
        run_publish(prepared)
    status = (store / "publish-status.json").read_text()
    assert "SECRET_CANARY" not in status and '"production": "UNKNOWN"' in status
    assert operator.state["target"]["commit"] == NEW
    assert operator.state["variables"]["STATIC_APPROVED_SHA"] == OLD
    assert "resume" not in operator.calls


@pytest.mark.parametrize(
    "status", ["queued", "in_progress", "waiting", "pending", "requested", None]
)
def test_real_idle_check_rejects_every_non_completed_state(tmp_path, status):
    operator = app.Operator(tmp_path)
    operator.gh = lambda endpoint: {
        "total_count": 1,
        "workflow_runs": [{"id": 1, "status": status}],
    }
    with pytest.raises(app.Blocked, match="not idle"):
        operator.idle()


def test_real_idle_check_reads_all_completed_hourly_history_without_ceiling(tmp_path):
    operator = app.Operator(tmp_path)
    calls = []

    def gh(endpoint):
        calls.append(endpoint)
        assert "status=" not in endpoint
        page = int(endpoint.split("page=")[-1])
        start = (page - 1) * 100
        return {
            "total_count": 4001,
            "workflow_runs": [
                {"id": index, "status": "completed"}
                for index in range(start, min(start + 100, 4001))
            ],
        }

    operator.gh = gh
    operator.idle()
    assert len(calls) == 41


def test_queued_to_waiting_transition_cannot_escape_unfiltered_inventory(tmp_path):
    operator = app.Operator(tmp_path)

    def gh(endpoint):
        assert "status=" not in endpoint
        # The run changed state before this snapshot. It still has the same ID
        # and must remain observable regardless of its non-completed state.
        return {"total_count": 1, "workflow_runs": [{"id": 7, "status": "waiting"}]}

    operator.gh = gh
    with pytest.raises(app.Blocked, match="not idle"):
        operator.idle()


@pytest.mark.parametrize(
    "response",
    [
        {"total_count": 1, "workflow_runs": []},
        {"total_count": 0, "workflow_runs": [{"id": 1}]},
        {"total_count": True, "workflow_runs": []},
        {"total_count": -1, "workflow_runs": []},
        {"total_count": 1001, "workflow_runs": []},
    ],
)
def test_unknown_or_truncated_active_inventory_never_proves_idle(tmp_path, response):
    operator = app.Operator(tmp_path)
    operator.gh = lambda endpoint: response
    with pytest.raises(app.Blocked, match="inventory|not idle"):
        operator.idle()


def test_native_command_failure_output_is_never_exposed(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 1, "SECRET_CANARY", "SECRET_CANARY"
        ),
    )
    with pytest.raises(app.Blocked) as error:
        app.command(["tool"])
    assert "SECRET_CANARY" not in str(error.value)


def test_build_container_receives_only_public_configuration(monkeypatch, tmp_path):
    operator = app.Operator(tmp_path / "tooling")
    operator.root.mkdir()
    script = operator.root / "scripts/static_ops/build_app_release.sh"
    script.parent.mkdir(parents=True)
    script.write_text("trusted build")
    operator.env = {
        "PATH": "/bin",
        "HOME": "/private",
        "CLOUDFLARE_API_TOKEN": "SECRET_CF",
        "GH_TOKEN": "SECRET_GH",
        "STATIC_GOOGLE_ACCESS_TOKEN": "SECRET_GOOGLE",
    }
    calls = []

    def command(args, **kwargs):
        calls.append((args, kwargs))
        if "clone" in args:
            Path(args[-1]).mkdir()
        if str(operator.root / "scripts/prepare_msm_feed.py") in args:
            return '{"runs":[{"run":"new"}]}'
        return ""

    monkeypatch.setattr(app, "command", command)
    monkeypatch.setattr(app, "restore", lambda origin, feed, now: feed.mkdir())
    monkeypatch.setattr(app, "validate_feed", lambda *args: {})
    monkeypatch.setattr(gate, "static_sources", lambda *args: None)
    monkeypatch.setattr(gate, "artifact", lambda *args: {"version": "1.0.0"})
    monkeypatch.setattr(gate, "upload_context", lambda *args: None)
    monkeypatch.setattr(operator, "validate_build", lambda *args: {})
    snapshot = SimulatedOperator(tmp_path).state
    operator.build(NEW, snapshot, tmp_path / "build")
    args, kwargs = next(item for item in calls if item[0][:2] == ["docker", "run"])
    assert not any("TOKEN" in value or "SECRET" in value for value in args)
    assert not any("TOKEN" in key for key in kwargs["env"])
    assert kwargs["input_text"] == "trusted build"
    assert "/feed,readonly" in " ".join(args)
    assert "--hours" in next(item[0] for item in calls if "--hours" in item[0])


def test_live_rules_and_source_contract_must_match_without_deploy(tmp_path):
    operator = app.Operator(tmp_path)
    calls = []
    operator.git = lambda *args: (
        "reviewed-rules" if args[-1].endswith(".rules") else "same-contract"
    )

    def rules_get(path):
        calls.append(path)
        if "/releases/" in path:
            return {"rulesetName": "projects/navmate-prod/rulesets/ruleset-id"}
        return {"source": {"files": [{"content": "reviewed-rules\n"}]}}

    operator.rules_get = rules_get
    assert operator.compatibility(NEW, OLD, {"VITE_FIREBASE_PROJECT_ID": "navmate-prod"})
    assert len(calls) == 3 and all(path.startswith("projects/navmate-prod/") for path in calls)
    operator.rules_get = lambda path: {"rulesetName": "projects/other/rulesets/ruleset-id"}
    with pytest.raises(app.Blocked, match="another project"):
        operator.compatibility(NEW, OLD, {"VITE_FIREBASE_PROJECT_ID": "navmate-prod"})


@pytest.mark.parametrize("path", app.COMPATIBILITY)
def test_rules_indexes_and_project_schema_change_need_separate_review(tmp_path, path):
    operator = app.Operator(tmp_path)
    operator.git = lambda *args: "changed" if args[-1] == f"{NEW}:{path}" else "old"
    with pytest.raises(app.Blocked, match="migration required"):
        operator.compatibility(NEW, OLD, {"VITE_FIREBASE_PROJECT_ID": "navmate-prod"})


def test_deployed_rules_change_between_reads_blocks(tmp_path):
    operator = app.Operator(tmp_path)
    operator.git = lambda *args: "same"
    replies = iter(
        [
            {"rulesetName": "projects/navmate-prod/rulesets/one"},
            {"source": {"files": [{"content": "same"}]}},
            {"rulesetName": "projects/navmate-prod/rulesets/two"},
        ]
    )
    operator.rules_get = lambda path: next(replies)
    with pytest.raises(app.Blocked, match="changed during read"):
        operator.compatibility(NEW, OLD, {"VITE_FIREBASE_PROJECT_ID": "navmate-prod"})


@pytest.mark.parametrize(
    "sha,current", [("main", OLD), (NEW, "v1.20.1"), ("b" * 39, OLD), (NEW, "A" * 40)]
)
def test_full_explicit_shas_fail_before_any_provider_or_command(tmp_path, sha, current):
    operator = app.Operator(tmp_path)
    operator.git = lambda *args: pytest.fail("Must not run commands on invalid SHA")
    with pytest.raises(app.Blocked, match="full"):
        operator.snapshot(sha, current)


def test_environment_shadowed_configuration_is_rejected(tmp_path):
    operator = app.Operator(tmp_path)

    def gh(endpoint):
        if endpoint.startswith("actions/variables/"):
            name = endpoint.split("/")[-1]
            return {"name": name, "value": "public"}
        return {"total_count": 1, "variables": [{"name": "STATIC_APPROVED_SHA", "value": NEW}]}

    operator.gh = gh
    with pytest.raises(app.Blocked, match="shadows"):
        operator.variables()


def test_isolated_upload_uses_supported_pages_command_and_disables_native_logs(
    monkeypatch, tmp_path
):
    operator = app.Operator(tmp_path)
    snapshot = SimulatedOperator(tmp_path).state
    snapshot["variables"]["STATIC_AUTOMATION_ENABLED"] = "false"
    calls = []
    monkeypatch.setattr(app, "command", lambda args, **kwargs: calls.append((args, kwargs)))
    operator.env = {
        "PATH": "/bin",
        "HOME": "/private",
        "CLOUDFLARE_API_TOKEN": "SECRET_CF",
        "GH_TOKEN": "SECRET_GH",
        "STATIC_GOOGLE_ACCESS_TOKEN": "SECRET_GOOGLE",
    }
    operator.snapshot = lambda *args, **kwargs: copy.deepcopy(snapshot)
    operator.validate_build = lambda *args: calls.append(("validate", {}))
    operator.upload(tmp_path / "fresh", NEW, snapshot)
    args, kwargs = calls[-1]
    assert args[1:] == [
        "pages",
        "deploy",
        "dist-static",
        "--project-name",
        "navmate",
        "--branch",
        "main",
        "--commit-hash",
        NEW,
    ]
    assert kwargs["cwd"] == tmp_path / "fresh/upload"
    assert "GH_TOKEN" not in kwargs["env"] and "STATIC_GOOGLE_ACCESS_TOKEN" not in kwargs["env"]
    assert kwargs["env"]["WRANGLER_WRITE_LOGS"] == "false"
    assert kwargs["env"]["CI"] == "true"
    assert calls[-2][0] == "validate"


@pytest.mark.parametrize(
    "change", [None, "content", "cache", "etag", "mime", "missing-200", "missing-403"]
)
def test_canonical_root_headers_bytes_and_missing_asset_gate(prepared, monkeypatch, change):
    from email.message import Message
    from urllib.error import HTTPError

    operator, store, _ = prepared
    real = app.Operator(store)
    dist = store / "prepared/upload/dist-static"
    urls = []

    class Response:
        status = 200

        def __init__(self, name):
            self.payload = (dist / name).read_bytes()
            self.headers = Message()
            self.headers["Content-Type"] = (
                "text/html" if name == "index.html" else "application/json"
            )
            self.headers["Cache-Control"] = "no-cache"
            self.headers["ETag"] = "hash"
            if change == "content":
                self.payload = b"wrong"
            if change in ("cache", "etag"):
                del self.headers["Cache-Control" if change == "cache" else "ETag"]
            if change == "mime":
                self.headers.replace_header("Content-Type", "text/plain")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, *args):
            return self.payload

    class Opener:
        def open(self, request, **kwargs):
            urls.append(request.full_url)
            if request.full_url.endswith(".missing.whl"):
                if change == "missing-200":
                    return Response("index.html")
                raise HTTPError(
                    request.full_url, 403 if change == "missing-403" else 404, "missing", {}, None
                )
            return Response(
                "index.html" if request.full_url.endswith("/") else "local/manifest.json"
            )

    monkeypatch.setattr(app, "build_opener", lambda *args: Opener())
    if change:
        with pytest.raises(app.Blocked):
            real.verify_serving(store / "prepared")
    else:
        real.verify_serving(store / "prepared")
        assert urls[0] == gate.ORIGIN + "/"
        assert urls[1] == gate.ORIGIN + "/local/manifest.json"
