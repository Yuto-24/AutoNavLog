"""App publication boundary: real inventory gates, simulated provider side effects."""

import copy
import io
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.static_ops import app_release as app
from scripts.static_ops import publication_gate as gate
from tests.unit.test_pages_target import deployment as pages_deployment
from tests.unit.test_pages_target import page as pages_page
from tests.unit.test_pages_target import project as pages_project
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

    def prepare_upload(self, destination, sha, snapshot):
        self.event("pre-upload")
        self.validate_build(destination, sha, snapshot)
        return {"isolated": "upload-env"}

    def upload(self, destination, sha, env):
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
        ("pre-upload", "UNCHANGED", "UNCHANGED"),
        ("validate-fresh", "UNCHANGED", "UNCHANGED"),
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
    assert "unconditional restart after failure" in status["required_action"]
    if failure == "resume":
        assert status["events"][-1] == {
            "action": "automation",
            "stage": "resume",
            "state": "ATTEMPTED",
        }
        assert status["changes"]["automation"] == "UNKNOWN"
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


@pytest.fixture
def pages_operator(monkeypatch, tmp_path):
    """Real target and paginated GETs; synthetic build and provider mutations only."""
    operator = SimulatedOperator(tmp_path / "tooling", automation="false")
    operator.env = {"CLOUDFLARE_API_TOKEN": "SYNTHETIC"}
    project = pages_project()
    project["source"] = None
    project["canonical_deployment"]["deployment_trigger"]["metadata"]["commit_hash"] = OLD
    rows = [copy.deepcopy(project["canonical_deployment"])]
    requests = []
    operator.project_reads = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET" and request.data is None
            parsed = urlsplit(request.full_url)
            path = f"/client/v4/accounts/{'c' * 32}/pages/projects/navmate"
            assert parsed.hostname == "api.cloudflare.com"
            requests.append(request.full_url)
            if parsed.path == path:
                observed = operator.project_reads.pop(0) if operator.project_reads else project
                payload = {"success": True, "result": copy.deepcopy(observed)}
            else:
                assert parsed.path == path + "/deployments"
                query = parse_qs(parsed.query)
                assert query["per_page"] == ["20"] and set(query) == {"page", "per_page"}
                page = int(query["page"][0])
                payload = pages_page(rows[(page - 1) * 20:page * 20], page, len(rows))
            return Response(json.dumps(payload).encode())

    client = app.probe_providers.Client
    monkeypatch.setattr(
        app.probe_providers, "Client", lambda env: client(env, opener=Opener())
    )
    snapshot = operator.snapshot

    def real_target_snapshot(*args, **kwargs):
        value = snapshot(*args, **kwargs)
        value["target"] = operator.target(value["variables"])
        return value

    operator.snapshot = real_target_snapshot
    return operator, project, rows, requests


def disabled_github_source():
    return {
        "type": "github",
        "config": {"production_deployments_enabled": False, "preview_deployment_setting": "none"},
    }


@pytest.mark.parametrize("source", [None, disabled_github_source()])
def test_real_target_allows_upload_method_with_git_builds_disabled(pages_operator, source):
    operator, project, rows, requests = pages_operator
    project["source"] = source
    rows.extend(pages_deployment(i) for i in range(2, 22))
    result = operator.target(operator.variables())
    assert result["id"] == project["canonical_deployment"]["id"]
    assert result["commit"] == OLD
    assert len([url for url in requests if "/deployments?" in url]) == 2
    assert len(requests) == 5  # Initial project, inventory's before/after project, two pages.


@pytest.mark.parametrize("pattern", ["AAA", "ABB", "AAB", "ABA"])
@pytest.mark.parametrize(
    "change", ["enabled", "allowed-source", "allowed-config", "bool-to-number", "functions"]
)
def test_real_target_binds_initial_settings_to_both_inventory_reads(
    pages_operator, pattern, change
):
    """All three GETs must agree, including allowed source changes and JSON types."""
    operator, project, _, requests = pages_operator
    project["source"] = disabled_github_source()
    changed = copy.deepcopy(project)
    if change == "enabled":
        changed["source"]["config"]["production_deployments_enabled"] = True
    elif change == "allowed-source":
        changed["source"] = None
    elif change == "allowed-config":
        changed["source"]["config"]["preview_branch_excludes"] = ["draft/*"]
    elif change == "bool-to-number":
        changed["source"]["config"]["production_deployments_enabled"] = 0
    else:
        changed["deployment_configs"]["production"]["kv_namespaces"] = {
            "PRIVATE_BINDING": {"namespace_id": "PRIVATE_VALUE"}
        }
    operator.project_reads = [copy.deepcopy(project if read == "A" else changed)
                              for read in pattern]
    if pattern == "AAA":
        baseline = operator.target(operator.variables())
        app.unchanged({"target": baseline}, {"target": operator.target(operator.variables())},
                      deployment=True)
    else:
        with pytest.raises(app.Blocked, match="Pages target unknown or changed") as error:
            operator.target(operator.variables())
        assert "PRIVATE" not in str(error.value)
        assert len(requests) == 4  # All three project reads and complete inventory.
    assert not operator.project_reads


@pytest.mark.parametrize("legacy", [True, False, "deprecated", 0, None])
def test_real_target_does_not_require_deprecated_deployments_flag(pages_operator, legacy):
    operator, project, _, _ = pages_operator
    project["source"] = disabled_github_source()
    project["source"]["config"]["deployments_enabled"] = legacy
    assert operator.target(operator.variables())["commit"] == OLD


@pytest.mark.parametrize(
    "source",
    [
        {}, [], "github", False, 0,
        {"type": "gitlab", "config": disabled_github_source()["config"]},
        {"config": disabled_github_source()["config"]},
        {"type": None, "config": disabled_github_source()["config"]},
        {"type": "github"},
        *({"type": "github", "config": config} for config in (None, [], "none", False, 0, {})),
    ],
)
def test_real_target_rejects_unknown_source_shapes(pages_operator, source):
    operator, project, _, requests = pages_operator
    project["source"] = source
    with pytest.raises(app.Blocked, match="Git deployments"):
        operator.target(operator.variables())
    assert len(requests) == 1


def test_real_target_requires_explicit_source_field(pages_operator):
    operator, project, _, _ = pages_operator
    del project["source"]
    with pytest.raises(app.Blocked, match="Git deployments"):
        operator.target(operator.variables())


@pytest.mark.parametrize(
    "key,value",
    [
        ("production_deployments_enabled", value)
        for value in (True, "false", 0, 1, None, [], {})
    ] + [
        ("preview_deployment_setting", value)
        for value in ("all", "custom", "unknown", "NONE", False, 0, None, [], {})
    ],
)
def test_real_target_rejects_enabled_unknown_or_mistyped_git_controls(
    pages_operator, key, value
):
    operator, project, _, _ = pages_operator
    project["source"] = disabled_github_source()
    project["source"]["config"][key] = value
    with pytest.raises(app.Blocked, match="Git deployments"):
        operator.target(operator.variables())


@pytest.mark.parametrize("key", ["production_deployments_enabled", "preview_deployment_setting"])
def test_real_target_rejects_missing_git_controls(pages_operator, key):
    operator, project, _, _ = pages_operator
    project["source"] = disabled_github_source()
    del project["source"]["config"][key]
    with pytest.raises(app.Blocked, match="Git deployments"):
        operator.target(operator.variables())


@pytest.mark.parametrize(
    "stage,state", [("build", "active"), ("queued", "idle"), ("new", "unknown")]
)
def test_real_target_disabled_git_still_rejects_busy_or_unknown_last_page(
    pages_operator, stage, state
):
    operator, project, rows, requests = pages_operator
    project["source"] = disabled_github_source()
    rows.extend(pages_deployment(i) for i in range(2, 21))
    rows.append(pages_deployment(21, stage, state, "preview"))
    with pytest.raises(app.Blocked, match="in flight"):
        operator.target(operator.variables())
    assert len([url for url in requests if "/deployments?" in url]) == 2


def test_source_drift_during_prepare_has_no_plan_or_provider_writes(pages_operator, tmp_path):
    operator, project, _, _ = pages_operator
    project["source"] = disabled_github_source()
    build = operator.build

    def build_then_drift(*args):
        result = build(*args)
        project["source"] = None  # Still allowed; its reviewed fingerprint must change.
        return result

    operator.build = build_then_drift
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    journal = app.Journal(store / "prepare-status.json", "prepare", NEW)
    with pytest.raises(app.Blocked, match="changed"):
        app.prepare(operator, NEW, OLD, store, journal)
    assert not (store / "plan.json").exists()
    assert journal.value["events"] == []
    assert set(journal.value["changes"].values()) == {"UNCHANGED"}
    assert not {"pause", "disable", "upload", "approve", "enable", "resume"} & set(operator.calls)


@pytest.mark.parametrize("phase", ["before-publish", "fresh-build"])
@pytest.mark.parametrize("change", ["safe-source", "source-config", "enabled-production"])
def test_source_drift_after_real_target_prepare_stops_publish_before_upload(
    pages_operator, tmp_path, phase, change
):
    operator, project, _, _ = pages_operator
    project["source"] = disabled_github_source()
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    initial = copy.deepcopy(project)
    journal = app.Journal(store / "prepare-status.json", "prepare", NEW)
    app.prepare(operator, NEW, OLD, store, journal)
    plan = json.loads((store / "plan.json").read_text())
    assert plan["snapshot"]["target"] == operator.target(operator.variables())
    assert project == initial
    assert operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "false"
    assert operator.workflow_state == "active"
    assert not {"pause", "disable", "upload", "approve", "enable", "resume"} & set(operator.calls)
    operator.calls.clear()

    def drift():
        if change == "safe-source":
            project["source"] = None
        elif change == "source-config":
            project["source"]["config"]["preview_branch_excludes"] = ["draft/*"]
        else:
            project["source"]["config"]["production_deployments_enabled"] = True

    if phase == "before-publish":
        drift()
    else:
        build = operator.build

        def build_then_drift(*args):
            result = build(*args)
            drift()
            return result

        operator.build = build_then_drift
    reviewed = app.digest((store / "plan.json").read_bytes())
    with pytest.raises(app.Blocked, match="changed|Git deployments"):
        run_publish((operator, store, reviewed))
    assert operator.uploaded is None
    assert not {"upload", "approve", "enable", "resume"} & set(operator.calls)
    assert operator.state["variables"]["STATIC_APPROVED_SHA"] == OLD
    assert operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "false"
    expected_workflow = "active" if phase == "before-publish" else "disabled_manually"
    assert operator.workflow_state == expected_workflow


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
    env = operator.prepare_upload(tmp_path / "fresh", NEW, snapshot)
    assert all(not isinstance(args, list) or "deploy" not in args for args, _ in calls)
    operator.upload(tmp_path / "fresh", NEW, env)
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
    "change", [None, "content", "length", "cache", "etag", "mime", "status", "forbidden",
               "missing-200", "missing-403"]
)
@pytest.mark.parametrize("resource", ["index.html", "local/manifest.json"])
def test_canonical_root_headers_bytes_and_missing_asset_gate(
    prepared, monkeypatch, change, resource
):
    """Honest operations identity must not bypass any serving or missing-asset gate."""
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
            if name != resource:
                return
            if change == "forbidden":
                raise HTTPError(gate.ORIGIN + "/" + name, 403, "forbidden", {}, None)
            if change == "status":
                self.status = 201
            if change == "content":
                self.payload = b"x" * len(self.payload)
            if change == "length":
                self.payload += b"x"
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
            assert request.get_method() == "GET" and request.data is None
            assert dict(request.header_items()) == {
                "Cache-control": "no-cache", "User-agent": "AutoNavLog-StaticOperations/1.0"
            }
            if request.full_url.endswith(".missing.whl"):
                if change == "missing-200":
                    return Response("index.html")
                raise HTTPError(
                    request.full_url, 403 if change == "missing-403" else 404, "missing", {}, None
                )
            return Response(
                "index.html" if request.full_url.endswith("/") else "local/manifest.json"
            )

    def build(handler):
        assert handler is app.NoRedirect
        return Opener()

    monkeypatch.setattr(app, "build_opener", build)
    if change:
        with pytest.raises(HTTPError if change == "forbidden" else app.Blocked):
            real.verify_serving(store / "prepared")
        if change == "forbidden":
            assert len(urls) == (1 if resource == "index.html" else 2)
    else:
        real.verify_serving(store / "prepared")
        assert urls[0] == gate.ORIGIN + "/"
        assert urls[1] == gate.ORIGIN + "/local/manifest.json"
        assert urls[2] == gate.ORIGIN + "/local/" + NEW + ".missing.whl"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("resource", ["/", "/local/manifest.json", "/missing.whl"])
def test_verify_serving_never_follows_redirects(prepared, monkeypatch, code, resource):
    """Reject redirects on HTML, manifest and the expected missing-asset response."""
    from email.message import Message

    _, store, _ = prepared
    dist = store / "prepared/upload/dist-static"
    calls = []

    class Opener:
        def open(self, request, **kwargs):
            calls.append(request.full_url)
            path = request.full_url.removeprefix(gate.ORIGIN)
            if path == resource or (resource == "/missing.whl" and path.endswith(".missing.whl")):
                app.NoRedirect().redirect_request(
                    request, None, code, "redirect", {}, "https://other.example/"
                )
                pytest.fail("Redirect must not be followed")
            name = "index.html" if path == "/" else "local/manifest.json"
            response = io.BytesIO((dist / name).read_bytes())
            response.status = 200
            response.headers = Message()
            response.headers["Content-Type"] = "text/html" if path == "/" else "application/json"
            response.headers["Cache-Control"] = "no-cache"
            response.headers["ETag"] = "hash"
            return response

    def build(handler):
        assert handler is app.NoRedirect
        return Opener()

    monkeypatch.setattr(app, "build_opener", build)
    with pytest.raises(ValueError, match="redirected"):
        app.Operator(store).verify_serving(store / "prepared")
    assert len(calls) == {"/": 1, "/local/manifest.json": 2, "/missing.whl": 3}[resource]


@pytest.mark.parametrize("failure", ["cli-install", "target-drift", "final-validation"])
def test_real_pre_upload_failures_leave_remote_production_unchanged(prepared, monkeypatch, failure):
    operator, store, _ = prepared
    real = app.Operator(operator.root)
    validations = []

    def validate(*args):
        validations.append(args)
        if failure == "final-validation" and len(validations) == 2:
            raise app.Blocked("Final feed validation failed")
        return operator.validate_build(*args)

    real.validate_build = validate

    def snapshot(*args, **kwargs):
        observed = copy.deepcopy(operator.state)
        if failure == "target-drift":
            observed["target"]["id"] = "competing-upload"
        return observed

    real.snapshot = snapshot

    def command(args, **kwargs):
        assert args[:2] == ["npm", "--prefix"]  # Never invoke Wrangler on failure.
        if failure == "cli-install":
            raise app.Blocked("CLI install failed")
        return ""

    monkeypatch.setattr(app, "command", command)
    operator.prepare_upload = real.prepare_upload
    with pytest.raises(app.Blocked):
        run_publish(prepared)
    status = json.loads((store / "publish-status.json").read_text())
    assert status["stage"] == "pre-upload"
    assert status["changes"]["production"] == "UNCHANGED"
    assert not any(event["action"] == "production" for event in status["events"])
    assert (
        operator.uploaded is None
        and "approve" not in operator.calls
        and "resume" not in operator.calls
    )


@pytest.fixture
def pinned_rules_operator(tmp_path):
    """Exact historical Rules bytes; no network, credentials or production writes."""
    transition = app.RULES_TRANSITION_V1227
    operator = app.Operator(tmp_path, rules_transition_v1227=True)
    old = (app.ROOT / "tests/fixtures/firestore-v1201.rules").read_bytes().decode("utf-8")
    new = (app.ROOT / "tests/fixtures/firestore-v1227.rules").read_bytes().decode("utf-8")
    assert app.digest(old.encode()) == transition["before_sha256"]
    assert app.digest(new.encode()) == transition["after_sha256"]
    sources = {transition["current_sha"]: old, transition["sha"]: new}
    operator.rules_source = lambda sha: sources[sha]
    operator.git = lambda *args: (
        sources[args[-1].split(":")[0]].strip() if args[-1].endswith(".rules") else "same-contract"
    )
    release = {
        "name": "projects/navmate-prod/releases/cloud.firestore",
        "rulesetName": "projects/navmate-prod/rulesets/new-ruleset",
        "updateTime": "2026-10-14T12:00:00Z",
    }
    calls = []

    def rules_get(path):
        calls.append(path)
        if "/releases/" in path:
            return copy.deepcopy(release)
        return {"source": {"files": [{"name": "firebase/firestore.rules", "content": new}]}}

    operator.rules_get = rules_get
    return operator, sources, release, calls


def pinned_compatibility(operator, *, sha=None, current=None, project="navmate-prod"):
    transition = app.RULES_TRANSITION_V1227
    return operator.compatibility(
        sha or transition["sha"],
        current or transition["current_sha"],
        {"VITE_FIREBASE_PROJECT_ID": project},
    )


def test_exact_v1227_transition_requires_explicit_opt_in(pinned_rules_operator):
    operator, _, _, calls = pinned_rules_operator
    operator.rules_transition_v1227 = False
    with pytest.raises(app.Blocked, match="migration required"):
        pinned_compatibility(operator)
    assert calls == []
    operator.rules_transition_v1227 = True
    assert pinned_compatibility(operator)
    receipt = operator.rules_receipt
    assert receipt["transition"] == app.RULES_TRANSITION_V1227
    assert receipt["source_sha256"] == app.RULES_TRANSITION_V1227["after_sha256"]
    assert receipt["deployment"] == "external; this script only reads Rules"
    assert len(calls) == 3 and calls[0] == calls[2]


@pytest.mark.parametrize("change", ["sha", "current", "project", "reverse", "same-source"])
def test_transition_opt_in_cannot_admit_other_release_or_project(pinned_rules_operator, change):
    operator, _, _, calls = pinned_rules_operator
    scope = dict(
        sha=app.RULES_TRANSITION_V1227["sha"],
        current=app.RULES_TRANSITION_V1227["current_sha"],
        project="navmate-prod",
    )
    if change in scope:
        scope[change] = "navmate-6b281" if change == "project" else "c" * 40
    elif change == "reverse":
        scope["sha"], scope["current"] = scope["current"], scope["sha"]
    else:
        scope["current"] = scope["sha"]
    with pytest.raises(app.Blocked, match="Only the pinned"):
        pinned_compatibility(operator, **scope)
    assert calls == []


@pytest.mark.parametrize("source", ["current_sha", "sha"])
@pytest.mark.parametrize("change", ["content", "whitespace", "crlf"])
def test_transition_rejects_changed_source_bytes(pinned_rules_operator, source, change):
    operator, sources, _, calls = pinned_rules_operator
    sha = app.RULES_TRANSITION_V1227[source]
    raw = sources[sha]
    sources[sha] = raw + ("// changed\n" if change == "content" else "\n")
    if change == "crlf":
        sources[sha] = raw.replace("\n", "\r\n")
    with pytest.raises(app.Blocked, match="source hash mismatch"):
        pinned_compatibility(operator)
    assert calls == []


@pytest.mark.parametrize("path", app.COMPATIBILITY[1:])
def test_pinned_transition_still_rejects_index_or_schema_changes(pinned_rules_operator, path):
    operator, _, _, calls = pinned_rules_operator
    original = operator.git
    operator.git = lambda *args: (
        "changed" if args[-1] == f"{app.RULES_TRANSITION_V1227['sha']}:{path}" else original(*args)
    )
    with pytest.raises(app.Blocked, match="migration required"):
        pinned_compatibility(operator)
    assert calls == []


@pytest.mark.parametrize(
    "change",
    ["old-rules", "extra-whitespace", "filename", "extra-file", "foreign-ruleset", "release-drift"],
)
def test_transition_requires_already_deployed_exact_rules_and_stable_release(
    pinned_rules_operator, change
):
    operator, sources, release, calls = pinned_rules_operator
    new = sources[app.RULES_TRANSITION_V1227["sha"]]

    def rules_get(path):
        calls.append(path)
        if "/releases/" in path:
            value = copy.deepcopy(release)
            if change == "foreign-ruleset":
                value["rulesetName"] = "projects/another-project/rulesets/new"
            if change == "release-drift" and len(calls) > 1:
                value["updateTime"] = "changed"
            return value
        content = (
            sources[app.RULES_TRANSITION_V1227["current_sha"]] if change == "old-rules" else new
        )
        if change == "extra-whitespace":
            content += "\n"
        files = [
            {
                "name": "unexpected.rules" if change == "filename" else "firebase/firestore.rules",
                "content": content,
            }
        ]
        if change == "extra-file":
            files.append(files[0])
        return {"source": {"files": files}}

    operator.rules_get = rules_get
    with pytest.raises(app.Blocked):
        pinned_compatibility(operator)
    assert operator.rules_receipt is None


@pytest.mark.parametrize("automation", ["true", "false"])
def test_real_snapshot_requires_transition_schedule_already_paused(
    pinned_rules_operator, monkeypatch, automation
):
    operator, _, _, _ = pinned_rules_operator
    transition = app.RULES_TRANSITION_V1227
    variables = {
        **{name: "public" for name in gate.CONFIGURATION},
        "STATIC_ORIGIN": gate.ORIGIN,
        "STATIC_APPROVED_SHA": transition["current_sha"],
        "STATIC_AUTOMATION_ENABLED": automation,
        "CLOUDFLARE_ACCOUNT_ID": "c" * 32,
        "VITE_FIREBASE_PROJECT_ID": "navmate-prod",
    }
    operator.variables = lambda: variables
    operator.gh = lambda endpoint: {"private": False, "full_name": app.REPO}
    operator.workflow = lambda: {"state": "active"}
    operator.idle = lambda: None
    operator.target = lambda vars: {"commit": transition["current_sha"], "id": "deployment"}
    operator.env["CLOUDFLARE_API_TOKEN"] = "SYNTHETIC"
    git = operator.git

    def source_git(*args):
        if args[0] == "status":
            return ""
        if args[0] == "remote":
            return "https://github.com/" + app.REPO + ".git"
        if args[0] == "rev-parse":
            return args[1].removesuffix("^{commit}")
        return git(*args)

    operator.git = source_git
    monkeypatch.setattr(
        app,
        "release",
        lambda origin, current: {
            "configuration": {name: variables[name] for name in gate.CONFIGURATION}
        },
    )
    if automation == "true":
        with pytest.raises(app.Blocked, match="Pause recurring"):
            operator.snapshot(transition["sha"], transition["current_sha"])
    else:
        value = operator.snapshot(transition["sha"], transition["current_sha"])
        assert value["rules_receipt"]["release"]["rulesetName"].endswith("/new-ruleset")
        assert value["rules_receipt"]["transition"] == transition


def test_rules_source_reader_preserves_raw_line_endings(tmp_path, monkeypatch):
    # CI excludes Git and deployable Firebase source. Verify the binary subprocess boundary.
    raw = b"rules_version = '2';\r\n// trailing whitespace \r\n"

    def read_source(args, **kwargs):
        assert not kwargs.get("text") and kwargs.get("encoding") is None
        return subprocess.CompletedProcess(args, 0, raw, b"")

    monkeypatch.setattr(subprocess, "run", read_source)
    assert app.Operator(tmp_path).rules_source("HEAD").encode() == raw


def test_prepare_and_publish_record_external_rules_and_keep_recurring_paused(tmp_path):
    store = tmp_path / "store"
    store.mkdir(mode=0o700)
    operator = SimulatedOperator(tmp_path / "tooling", automation="false")
    operator.state["rules_receipt"] = {
        "transition": copy.deepcopy(app.RULES_TRANSITION_V1227),
        "release": {"rulesetName": "projects/navmate-prod/rulesets/new"},
        "source_sha256": app.RULES_TRANSITION_V1227["after_sha256"],
        "deployment": "external; this script only reads Rules",
    }
    journal = app.Journal(store / "prepare-status.json", "prepare", NEW)
    app.prepare(operator, NEW, OLD, store, journal)
    expected = operator.state["rules_receipt"]
    assert json.loads((store / "prepare-status.json").read_text())["rules_receipt"] == expected
    result = run_publish((operator, store, app.digest((store / "plan.json").read_bytes())))
    assert result["rules_receipt"] == expected
    assert "resume" not in operator.calls
    assert "rules" not in result["changes"]  # External Rules deployment is never our mutation.


def test_transition_receipt_drift_before_publish_blocks_all_mutations(prepared):
    operator, store, _ = prepared
    operator.state["rules_receipt"] = {"transition": app.RULES_TRANSITION_V1227}
    with pytest.raises(app.Blocked, match="changed"):
        run_publish(prepared)
    assert "pause" not in operator.calls and operator.uploaded is None


def test_transition_rules_drift_after_upload_keeps_old_approval_and_stops(prepared):
    operator, _, _ = prepared
    operator.fail = "rules"
    with pytest.raises(app.Blocked):
        run_publish(prepared)
    assert operator.state["variables"]["STATIC_APPROVED_SHA"] == OLD
    assert operator.workflow_state == "disabled_manually"
    assert operator.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "false"
