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


def test_real_idle_check_requires_complete_stable_pagination(tmp_path):
    operator = app.Operator(tmp_path)
    first = [{"id": i, "status": "completed"} for i in range(100)]
    responses = iter(
        [
            {"total_count": 101, "workflow_runs": first},
            {"total_count": 101, "workflow_runs": [{"id": 101, "status": "completed"}]},
        ]
    )
    operator.gh = lambda endpoint: next(responses)
    operator.idle()
    operator.gh = lambda endpoint: {"total_count": 101, "workflow_runs": first[:1]}
    with pytest.raises(app.Blocked, match="Incomplete"):
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
