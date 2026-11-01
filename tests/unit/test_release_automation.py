"""Release orchestration: mocked APIs and real existing publication state machine."""

import copy
import json
import sys
from pathlib import Path

import pytest

from scripts.static_ops import app_release as app
from scripts.static_ops import release_automation as auto
from tests.unit.test_static_app_release import NEW, OLD, SimulatedOperator

RELEASE = {"id": 123, "tag_name": "v1.23.0", "draft": False, "prerelease": False,
           "published_at": "2026-10-28T00:00:00Z"}
REPO = {"full_name": app.REPO, "private": False}


class Source(app.Operator):
    def __init__(self, version="1.23.0"):
        super().__init__(Path("/tmp"))
        self.version = version
        self.selected = {**RELEASE, "tag_name": "v" + version}
        self.run = {"status": "completed", "conclusion": "success", "event": "push",
                    "path": auto.PUBLISHER, "head_repository": REPO,
                    "head_branch": "release/1.x" if version.startswith("1.") else "main",
                    "head_sha": NEW}
        self.tag = {"type": "commit", "sha": NEW}
        self.calls = []
        self.fetch_sha = NEW
        self.source_version = version
        self.ancestor = True

    def gh(self, path):
        self.calls.append(path)
        if path.startswith("actions/runs/"):
            return copy.deepcopy(self.run)
        if path.startswith("releases/"):
            return copy.deepcopy(self.selected)
        if path.startswith("git/ref/tags/"):
            return {"object": self.tag}
        if path.startswith("git/tags/"):
            return {"object": {"type": "commit", "sha": NEW}}
        pytest.fail(path)

    def git(self, *args):
        self.calls.append(args)
        if args[0] == "show":
            return self.source_version
        if args[0] == "rev-parse":
            return self.fetch_sha
        if args[0] == "merge-base":
            app.require(self.ancestor, "Not on release line")
        return ""


@pytest.mark.parametrize("version", ["1.23.0", "2.1.0"])
@pytest.mark.parametrize("trigger", ["workflow_run", "release", "workflow_dispatch"])
def test_real_release_and_tag_selected_for_both_lines(version, trigger):
    op = Source(version)
    event = {"repository": REPO,
             "action": "completed" if trigger == "workflow_run" else "published",
             "workflow_run": {"id": 42}, "release": op.selected,
             "inputs": {"release_id": "123"}}
    selected = auto.select_release(op, event, trigger)
    assert auto.candidate(op, selected) == NEW
    assert selected["tag_name"] == "v" + version
    assert not any("PATCH" in str(call) for call in op.calls)


@pytest.mark.parametrize("field,value", [("draft", True), ("prerelease", True),
    ("published_at", None), ("id", True), ("id", 0), ("tag_name", "v2.1.0-rc.1"),
    ("tag_name", "v2.01.0"), ("tag_name", "$(bad)")])
def test_unpublished_or_nonstable_releases_fail(field, value):
    with pytest.raises(app.Blocked):
        auto.stable({**RELEASE, field: value})


@pytest.mark.parametrize("field,value", [("status", "in_progress"), ("conclusion", "failure"),
    ("event", "pull_request"), ("path", ".github/workflows/test.yml"),
    ("head_repository", {"full_name": "fork/repo"}), ("head_branch", "dev"),
    ("head_sha", "main")])
def test_bridge_never_trusts_failed_foreign_or_unrelated_runs(field, value):
    op = Source()
    op.run[field] = value
    with pytest.raises(app.Blocked, match="trusted release publisher"):
        auto.select_release(op, {"repository": REPO, "action": "completed",
                                 "workflow_run": {"id": 42}}, "workflow_run")


@pytest.mark.parametrize("damage", ["deleted", "changed", "moved", "version", "foreign"])
def test_candidate_revalidates_release_tag_version_and_ancestry(damage):
    op = Source()
    if damage == "deleted":
        op.selected["draft"] = True
    if damage == "changed":
        op.selected["id"] = 456
    if damage == "moved":
        op.fetch_sha = OLD
    if damage == "version":
        op.source_version = "1.22.0"
    if damage == "foreign":
        op.ancestor = False
    with pytest.raises(app.Blocked):
        auto.candidate(op, RELEASE)


def test_annotated_tag_peels_to_commit():
    op = Source()
    op.tag = {"type": "tag", "sha": OLD}
    assert auto.candidate(op, RELEASE) == NEW
    assert "git/tags/" + OLD in op.calls


@pytest.mark.parametrize("version,target,sha,current,expected", [
    ("1.22.0", "1.23.0", NEW, OLD, "PUBLISH"),
    ("1.23.0", "2.0.0", NEW, OLD, "PUBLISH"),
    ("2.0.0", "1.99.0", NEW, OLD, "SUPERSEDED"),
    ("1.24.0", "1.23.0", NEW, OLD, "SUPERSEDED"),
    ("1.23.0", "1.23.0", OLD, OLD, "ALREADY_CURRENT"),
    ("1.9.0", "1.10.0", NEW, OLD, "PUBLISH"),
])
def test_global_semver_prevents_duplicates_out_of_order_releases_and_v1_after_v2(
    version, target, sha, current, expected
):
    assert auto.disposition(target, sha, {"version": version, "commit": current, "dirty": False},
                            current) == expected


@pytest.mark.parametrize("damage", ["version-collision", "partial-upload", "dirty", "unknown"])
def test_uncertain_production_cannot_be_silently_retried(damage):
    live = {"version": "1.23.0", "commit": OLD, "dirty": False}
    if damage == "partial-upload":
        live["commit"] = NEW
    if damage == "dirty":
        live["dirty"] = True
    if damage == "unknown":
        live["version"] = "unknown"
    with pytest.raises((app.Blocked, ValueError)):
        auto.disposition("1.23.0", NEW, live, OLD)


def test_policy_uses_existing_plan_and_fresh_build_only(tmp_path):
    op = SimulatedOperator(tmp_path)
    auto.execute(op, NEW, OLD, tmp_path)
    assert op.uploaded == tmp_path / "fresh"
    assert op.calls.index("prepare-build") < op.calls.index("pause") < op.calls.index("fresh-build")
    assert op.calls.index("verify") < op.calls.index("approve") < op.calls.index("resume")
    assert op.state["variables"]["STATIC_APPROVED_SHA"] == NEW
    assert op.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "true"


@pytest.mark.parametrize("stage", ["snapshot", "prepare-build", "fresh-build", "upload", "verify",
                                    "approve", "enable", "resume"])
def test_every_failure_stops_and_never_automatically_rolls_back(tmp_path, stage):
    op = SimulatedOperator(tmp_path)
    op.fail = stage
    with pytest.raises(app.Blocked):
        auto.execute(op, NEW, OLD, tmp_path)
    assert op.calls.count("upload") <= 1
    assert "rollback" not in op.calls
    if stage in ("fresh-build", "upload", "verify", "approve", "enable", "resume"):
        assert op.state["variables"]["STATIC_AUTOMATION_ENABLED"] == "false"


@pytest.mark.parametrize("event", ["release", "workflow_run", "workflow_dispatch"])
def test_actions_lease_allows_queue_behind_self_but_requires_live_workflow(tmp_path, event):
    op = auto.ActionsOperator(RELEASE, tmp_path)
    op.run_id = "42"
    op.env.update(GITHUB_ACTIONS="true", GITHUB_REPOSITORY=app.REPO,
                  RUNNER_ENVIRONMENT="github-hosted")
    op.gh = lambda path: {"path": auto.DEPLOYER, "status": "in_progress", "event": event}
    op.idle()
    op.gh = lambda path: {"path": auto.PUBLISHER, "status": "in_progress", "event": event}
    with pytest.raises(app.Blocked, match="lease"):
        op.idle()


def test_manual_operator_blocks_active_auto_release(tmp_path):
    op = app.Operator(tmp_path)
    op.gh = lambda path: {"total_count": 1, "workflow_runs": [{"id": 42,
        "status": "in_progress" if app.APP_WORKFLOW in path else "completed"}]}
    with pytest.raises(app.Blocked, match="not idle"):
        op.idle()


@pytest.mark.parametrize("path", app.COMPATIBILITY)
def test_even_whitespace_only_contract_changes_stop_before_any_build(tmp_path, path):
    op = app.Operator(tmp_path)
    op.rules_source = lambda sha: "same\n" + (" " if sha == NEW and path.endswith(".rules") else "")
    op.git = lambda *args: ("e" if args[-1] == NEW + ":" + path else "f") * 40
    op.rules_get = lambda *args: pytest.fail("Must stop before remote Rules read")
    with pytest.raises(app.Blocked, match="migration required"):
        op.compatibility(NEW, OLD, {"VITE_FIREBASE_PROJECT_ID": "navmate-prod"})


def test_automation_workflow_contract():
    workflow = (auto.ROOT / ".github/workflows/static-app-release.yml").read_text()
    triggers = workflow.split("\non:\n")[1].split("\npermissions:")[0]
    assert all(event not in triggers for event in ("push:", "pull_request", "schedule:"))
    assert "types: [published]" in triggers
    assert "workflows: [publish-github-release]" in triggers
    assert "vars.STATIC_APP_RELEASE_ENABLED == 'true'" in workflow
    assert "environment: static-production" in workflow and "static-publication" not in workflow
    assert "ref: main" in workflow and "persist-credentials: false" in workflow
    assert workflow.count("create_credentials_file: false") == 2
    assert workflow.count("https://www.googleapis.com/auth/firebase.readonly") == 2
    for name in ("static-app-release", "static-production"):
        source = (auto.ROOT / f".github/workflows/{name}.yml").read_text()
        assert "group: static-production\n  cancel-in-progress: false\n  queue: max" in source


@pytest.mark.parametrize("version,branch", [("1.23.0", "release/1.x"), ("2.1.0", "main")])
def test_source_branch_contract(version, branch):
    assert Source(version).source_branch(NEW) == branch


@pytest.mark.parametrize("version,current,expected", [
    ("1.22.0", OLD, ["prepare", "publish"]),
    ("1.23.0", NEW, []),
    ("1.24.0", OLD, []),
    ("2.0.0", OLD, []),
])
def test_cli_phases_keep_selection_private_and_skip_duplicate_or_stale_events(
    tmp_path, monkeypatch, version, current, expected
):
    source = Source()
    source.variables = lambda: {"STATIC_AUTOMATION_ENABLED": "true", "STATIC_APPROVED_SHA": current}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"repository": REPO, "action": "published",
                                     "release": RELEASE}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "release")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(auto.app, "Operator", lambda: source)
    monkeypatch.setattr(auto, "ActionsOperator", lambda *args: source)
    monkeypatch.setattr(auto.app, "release", lambda *args:
                        {"version": version, "commit": current, "dirty": False})
    calls = []
    monkeypatch.setattr(auto, "execute", lambda *args, phases: calls.extend(phases))
    for phase in ("prepare", "publish"):
        monkeypatch.setattr(sys, "argv", ["release_automation.py", phase])
        assert auto.main() == 0
    assert calls == expected
    store = auto.release_store()
    assert store.stat().st_mode & 0o077 == 0
    assert (store / "selection.json").stat().st_mode & 0o077 == 0


def test_phase_separation_retains_plan_drift_gate(tmp_path):
    op = SimulatedOperator(tmp_path)
    auto.execute(op, NEW, OLD, tmp_path, phases=("prepare",))
    op.state["rules_digest"] = "changed-after-prepare"
    with pytest.raises(app.Blocked, match="changed"):
        auto.execute(op, NEW, OLD, tmp_path, phases=("publish",))
    assert "pause" not in op.calls and "upload" not in op.calls


@pytest.mark.parametrize("url,accepted", [
    ("https://github.com/Yuto-24/AutoNavLog", True),  # actions/checkout HTTPS origin
    ("https://github.com/Yuto-24/AutoNavLog.git", True),
    ("git@github.com:Yuto-24/AutoNavLog.git", True),
    ("https://github.com/other/AutoNavLog", False),
    ("https://github.com/Yuto-24/AutoNavLog.evil", False),
    ("https://github.com.evil/Yuto-24/AutoNavLog", False),
])
def test_checkout_origin_forms_reach_source_gate_without_remote_reconfiguration(
    tmp_path, url, accepted
):
    op = app.Operator(tmp_path)
    op.git = lambda *args: url if args[0] == "remote" else ""

    def source_gate(sha):
        raise LookupError("Reached source gate")

    op.source_branch = source_gate
    with pytest.raises(LookupError if accepted else app.Blocked,
                       match="source gate" if accepted else "repository remote"):
        op.snapshot(NEW, OLD)
