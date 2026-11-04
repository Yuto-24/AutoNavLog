"""Stable GitHub Release -> existing pinned prepare/plan/publish protocol.

Run only under the workflow's shared static-production concurrency lease. Never
deploy Rules, infer compatibility for changed contracts, or recover partial writes.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.release_content import parse_version_source, version_tuple  # noqa: E402
from scripts.static_ops import app_release as app  # noqa: E402

PUBLISHER = ".github/workflows/publish-github-release.yml"
DEPLOYER = ".github/workflows/" + app.APP_WORKFLOW


def stable(value: dict) -> dict:
    app.require(
        type(value.get("id")) is int and value["id"] > 0
        and value.get("draft") is False and value.get("prerelease") is False
        and isinstance(value.get("published_at"), str) and value["published_at"]
        and isinstance(value.get("tag_name"), str)
        and re.fullmatch(r"v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)",
                         value["tag_name"]),
        "Published stable versioned Release required",
    )
    return {key: value[key] for key in ("id", "tag_name", "published_at", "draft", "prerelease")}


def resolve_tag(operator: app.Operator, tag: str) -> str:
    obj = operator.gh("git/ref/tags/" + tag)["object"]
    for _ in range(16):
        app.require(isinstance(obj.get("sha"), str)
                    and re.fullmatch(r"[0-9a-f]{40}", obj["sha"]), "Invalid release tag SHA")
        if obj["type"] == "commit":
            return obj["sha"]
        app.require(obj["type"] == "tag", "Release tag does not resolve to a commit")
        obj = operator.gh("git/tags/" + obj["sha"])["object"]
    raise app.Blocked("Release tag nesting exceeds bound")


def select_release(operator: app.Operator, event: dict, name: str) -> dict:
    app.require(event.get("repository", {}).get("full_name") == app.REPO
                and event["repository"].get("private") is False, "Unexpected event repository")
    if name == "workflow_run":
        run_id = event["workflow_run"]["id"]
        app.require(type(run_id) is int and run_id > 0, "Invalid publisher run")
        run = operator.gh(f"actions/runs/{run_id}")
        app.require(
            event.get("action") == "completed" and run.get("status") == "completed"
            and run.get("conclusion") == "success" and run.get("event") == "push"
            and run.get("path") == PUBLISHER
            and run.get("head_repository", {}).get("full_name") == app.REPO
            and run.get("head_branch") in ("main", "release/1.x")
            and re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")),
            "Successful trusted release publisher run required",
        )
        branch, head = run["head_branch"], run["head_sha"]
        operator.git("fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        operator.git("merge-base", "--is-ancestor", head, "origin/" + branch)
        version = parse_version_source(operator.git("show", head + ":VERSION"))
        app.require(branch == ("release/1.x" if version_tuple(version)[0] == 1 else "main"),
                    "Publisher version line mismatch")
        selected = stable(operator.gh("releases/tags/v" + version))
        tagged = resolve_tag(operator, selected["tag_name"])
        operator.git("merge-base", "--is-ancestor", tagged, head)
        app.require(operator.git("show", tagged + ":VERSION") == version,
                    "Publisher tag version mismatch")
        return selected
    app.require(name in ("release", "workflow_dispatch"), "Unsupported publication event")
    if name == "release":
        app.require(event.get("action") == "published",
                    "Only published Releases trigger deployment")
        selected = stable(event["release"])
        live = stable(operator.gh("releases/" + str(selected["id"])))
        app.require(selected == live, "Release event changed")
        return live
    release_id = event.get("inputs", {}).get("release_id", "")
    app.require(isinstance(release_id, str) and re.fullmatch(r"[1-9]\d{0,19}", release_id),
                "Explicit existing Release ID required for replay")
    return stable(operator.gh("releases/" + release_id))


def candidate(operator: app.Operator, selected: dict) -> str:
    app.require(stable(operator.gh("releases/" + str(selected["id"]))) == selected,
                "Release changed or withdrawn")
    sha = resolve_tag(operator, selected["tag_name"])
    # Fetch only the selected ref; never execute tools or hooks from the release.
    operator.git("fetch", "origin", "refs/tags/" + selected["tag_name"])
    app.require(operator.git("rev-parse", "FETCH_HEAD^{commit}") == sha, "Release tag moved")
    app.require(operator.git("show", sha + ":VERSION") == selected["tag_name"][1:],
                "Tag and VERSION disagree")
    branch = operator.source_branch(sha)
    operator.git("fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    operator.git("merge-base", "--is-ancestor", sha, "origin/" + branch)
    return sha


def disposition(target: str, sha: str, canonical: dict, current: str) -> str:
    app.require(canonical.get("commit") == current and canonical.get("dirty") is False,
                "Canonical and approved application differ; reconcile before replay")
    before, after = version_tuple(canonical["version"]), version_tuple(target)
    app.require(after[0] in (1, 2), "Unsupported release line")
    if before == after:
        app.require(sha == current, "Same version points to a different commit")
        return "ALREADY_CURRENT"
    return "SUPERSEDED" if after < before else "PUBLISH"


class ActionsOperator(app.Operator):
    """The workflow owns static-production's lease, including while feed is paused."""

    def __init__(self, selected: dict, root: Path = ROOT):
        super().__init__(root)
        self.selected = selected
        self.run_id = self.env.get("GITHUB_RUN_ID", "")

    def idle(self) -> None:
        # The manual Operator instead rejects every non-completed run of BOTH
        # workflows. Queued feed/app runs here wait behind our shared lease.
        app.require(re.fullmatch(r"[1-9]\d*", self.run_id), "Actions run identity required")
        run = self.gh("actions/runs/" + self.run_id)
        app.require(
            self.env.get("GITHUB_ACTIONS") == "true"
            and self.env.get("GITHUB_REPOSITORY") == app.REPO
            and self.env.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and run.get("path") == DEPLOYER and run.get("status") == "in_progress"
            and run.get("event") in ("release", "workflow_run", "workflow_dispatch"),
            "Trusted app workflow concurrency lease required",
        )

    def snapshot(self, sha: str, current: str, *, paused: bool = False) -> dict:
        app.require(self.gh("actions/variables/STATIC_APP_RELEASE_ENABLED").get("value") == "true",
                    "Unattended app release opt-in missing or withdrawn")
        app.require(candidate(self, self.selected) == sha, "Selected release SHA changed")
        value = super().snapshot(sha, current, paused=paused)
        canonical = app.release(app.gate.ORIGIN, current)
        app.require(disposition(value["version"], sha, canonical, current) == "PUBLISH",
                    "Release no longer newer than production")
        value["github_release"] = self.selected
        return value


def execute(operator: ActionsOperator, sha: str, current: str, store: Path,
            *, phases: tuple[str, ...] = ("prepare", "publish")) -> None:
    """A new plan per attempt; no cached build reuse or partial-write recovery."""
    for mode in phases:
        journal = app.Journal(store / (mode + "-status.json"), mode, sha)
        journal.value["authorization"] = "stable-release policy; unchanged Rules/indexes/schema"
        try:
            if mode == "prepare":
                app.prepare(operator, sha, current, store, journal)
                # This is a policy-authorized plan, not a human approval attestation.
                journal.value["plan_sha256"] = app.digest((store / "plan.json").read_bytes())
            else:
                app.publish(operator, sha, current, store,
                            app.digest((store / "plan.json").read_bytes()), journal)
        except BaseException:
            journal.finish(False)
            raise
        journal.finish(True)


def main() -> int:
    if sys.argv[1:] == ["report"]:
        store = release_store()
        for mode in ("prepare", "publish"):
            path = store / (mode + "-status.json")
            if path.exists():
                value = json.loads(path.read_text())
                summary = {key: value.get(key) for key in
                           ("mode", "sha", "status", "stage", "changes", "reason")}
                print(json.dumps(summary))
        return 0
    app.require(len(sys.argv) == 2 and sys.argv[1] in ("prepare", "publish"),
                "Explicit prepare/publish phase required")
    mode = sys.argv[1]
    store = release_store()
    if mode == "publish":
        state = json.loads((store / "selection.json").read_text())
        if state["result"] != "PUBLISH":
            return 0
        execute(ActionsOperator(state["release"]), state["sha"], state["current_sha"], store,
                phases=("publish",))
        return 0
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    selected = select_release(app.Operator(), event, os.environ["GITHUB_EVENT_NAME"])
    operator = ActionsOperator(selected)
    sha = candidate(operator, selected)
    variables = operator.variables()
    app.require(variables.get("STATIC_AUTOMATION_ENABLED") == "true",
                "Forecast automation must be active before unattended app release")
    current = variables["STATIC_APPROVED_SHA"]
    canonical = app.release(app.gate.ORIGIN, current)
    result = disposition(selected["tag_name"][1:], sha, canonical, current)
    state = {"release": selected, "sha": sha, "current_sha": current, "result": result}
    store.mkdir(mode=0o700, exist_ok=False)
    app.write_json(store / "selection.json", state)
    print(json.dumps(state))
    if result != "PUBLISH":
        return 0
    execute(operator, sha, current, store, phases=("prepare",))
    return 0


def release_store() -> Path:
    return Path(os.environ["RUNNER_TEMP"]) / ("app-release-" + os.environ["GITHUB_RUN_ID"]
                                              + "-" + os.environ["GITHUB_RUN_ATTEMPT"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except app.Blocked as error:
        raise SystemExit("App Release BLOCKED: " + str(error)) from None
    except Exception:
        raise SystemExit(
            "App Release BLOCKED: inspect private journal and reconcile remote state"
        ) from None
