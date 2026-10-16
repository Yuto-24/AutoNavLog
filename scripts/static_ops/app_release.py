"""Prepare a pinned app release; publish only an explicitly reviewed plan.

No login, settings repair, rollback or failure-time scheduler restart is performed.
Provider command output is captured privately and never copied into reports.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from scripts.static_ops import pages_target, probe_providers  # noqa: E402
from scripts.static_ops import publication_gate as gate  # noqa: E402
from scripts.static_ops.operations import (  # noqa: E402
    NoRedirect,
    instant,
    monitor,
    release,
    restore,
)
from scripts.static_ops.refresh_feed import PUBLICATION_GENERATION_HOURS  # noqa: E402
from scripts.static_ops.validate_feed import validate_feed  # noqa: E402

REPO = "Yuto-24/AutoNavLog"
WORKFLOW = "static-production.yml"
VARIABLES = (
    *gate.CONFIGURATION,
    "CLOUDFLARE_ACCOUNT_ID",
    "STATIC_ORIGIN",
    "STATIC_APPROVED_SHA",
    "STATIC_AUTOMATION_ENABLED",
)
COMPATIBILITY = (
    "firebase/firestore.rules",
    "firebase/firestore.indexes.json",
    "data/schemas/project.schema.json",
)
# Only the emulator-verified v1.20.1 -> v1.22.7 transition is admitted by the opt-in.
# Owner-crafted generation reuse is tracked separately in #262; Rules stay unchanged.
RULES_TRANSITION_V1227 = {
    "current_sha": "a4de2a226dfa205b8be42fb62a710a1b06271ed8",
    "sha": "e0ad634a8fab2ed48310f48b08c4ec3961cad14b",
    "project": "navmate-prod",
    "before_sha256": "f27ee41870413f48b9ac0da609350cac3b3cc3d2d3a4fa0d30772b3312865246",
    "after_sha256": "574725b83fc0d0bca5df7311b970f97aeaaae068524eb1343bdc3912973757d8",
    "previous_ruleset": "projects/navmate-prod/rulesets/688d5522-783a-452c-bbaf-7981748d3ef0",
    "recovery": "forward-fix; no automatic old Rules rollback",
}


class Blocked(Exception):
    """Fixed messages only; native API/command errors must not escape."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise Blocked(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(value: object) -> str:
    return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def command(
    args: list[str],
    *,
    cwd: Path = ROOT,
    env: dict | None = None,
    input_text: str | None = None,
    timeout: int = 180,
) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        require(
            result.returncode == 0, "Command failed; inspect locally without exposing credentials"
        )
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        raise Blocked("Command unavailable or timed out") from None


def write_json(path: Path, value: object) -> None:
    # Replace atomically; the release store must be private and outside the checkout.
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class Journal:
    def __init__(self, path: Path, mode: str, sha: str):
        self.path = path
        self.value = {
            "mode": mode,
            "sha": sha,
            "status": "RUNNING",
            "stage": "preflight",
            "changes": {
                "automation": "UNCHANGED",
                "workflow": "UNCHANGED",
                "production": "UNCHANGED",
                "approved_sha": "UNCHANGED",
            },
            "events": [],
        }
        self.save()

    def save(self) -> None:
        write_json(self.path, self.value)

    def stage(self, name: str) -> None:
        self.value["stage"] = name
        self.save()

    def mutation(self, name: str, action) -> None:
        self.value["changes"][name] = "UNKNOWN"
        self.value["events"].append(
            {"action": name, "stage": self.value["stage"], "state": "ATTEMPTED"}
        )
        self.save()  # A timeout/interruption can occur after the remote mutation succeeded.
        action()
        self.value["changes"][name] = "CONFIRMED"
        self.value["events"][-1]["state"] = "CONFIRMED"
        self.save()

    def finish(self, success: bool) -> None:
        self.value["status"] = "OK" if success else "BLOCKED"
        self.value["required_action"] = (
            "Review plan; publication needs separate authorization"
            if success and self.value["mode"] == "prepare"
            else "None"
            if success
            else "Inspect recorded stage and remote state. Keep recurring publication paused after "
            "any pause/upload attempt where possible; a timed-out resume may already have "
            "taken effect. "
            "Verify canonical inventory and approved SHA before a "
            "separately authorized recovery/resume. No automatic rollback or unconditional "
            "restart after failure was attempted."
        )
        self.save()
        print(json.dumps(self.value, indent=2))


class Operator:
    def __init__(self, root: Path = ROOT, *, rules_transition_v1227: bool = False):
        self.root = root
        self.rules_transition_v1227 = rules_transition_v1227
        self.rules_receipt: dict | None = None
        self.env = dict(os.environ)
        self.wrangler = root / "services/taf-proxy/node_modules/.bin/wrangler"

    def git(self, *args: str) -> str:
        return command(["git", *args], cwd=self.root).strip()

    def rules_source(self, sha: str) -> str:
        # Text-mode subprocess output normalizes CRLF; this transition pins exact UTF-8 bytes.
        try:
            result = subprocess.run(
                ["git", "show", f"{sha}:firebase/firestore.rules"],
                cwd=self.root,
                capture_output=True,
                timeout=180,
                check=False,
            )
            require(result.returncode == 0, "Pinned Rules source unavailable")
            return result.stdout.decode("utf-8")
        except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
            raise Blocked("Pinned Rules source unavailable") from None

    def gh(self, endpoint: str, method: str = "GET", body: dict | None = None):
        args = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            ("repos/" + REPO + "/" + endpoint).rstrip("/"),
        ]
        if body is not None:
            args += ["--input", "-"]
        output = command(
            args,
            env={**self.env, "GH_PROMPT_DISABLED": "1"},
            input_text=json.dumps(body) if body is not None else None,
        )
        return json.loads(output) if output.strip() else None

    def variables(self) -> dict:
        result = {}
        for name in VARIABLES:
            item = self.gh("actions/variables/" + name)
            require(
                item.get("name") == name and isinstance(item.get("value"), str),
                "Missing repository variable",
            )
            result[name] = item["value"]
        # Environment shadowing could make resumed feed builds use another SHA/config.
        for environment in ("static-production", "static-publication", "static-validation"):
            # Reject environment shadowing using a complete variable inventory.
            page, count, seen = 1, None, set()
            while True:
                value = self.gh(f"environments/{environment}/variables?per_page=100&page={page}")
                rows = value["variables"]
                total = value["total_count"]
                require(
                    type(total) is int
                    and total >= 0
                    and isinstance(rows, list)
                    and (count is None or count == total),
                    "Environment inventory changed",
                )
                count = total
                for row in rows:
                    require(row["name"] not in seen, "Duplicate environment variable")
                    seen.add(row["name"])
                    require(
                        row["name"] not in VARIABLES, "Environment shadows publication variables"
                    )
                require(len(seen) <= total, "Invalid environment inventory")
                if len(seen) == total:
                    break
                require(len(rows) == 100 and page < 40, "Incomplete environment inventory")
                page += 1
        return result

    def workflow(self) -> dict:
        return self.gh("actions/workflows/" + WORKFLOW)

    def idle(self) -> None:
        # A single unfiltered inventory cannot lose queued -> waiting transitions
        # between separate status queries. Scan every declared page without an
        # arbitrary historical ceiling; incomplete/changed history fails closed.
        page, count, seen = 1, None, set()
        while True:
            value = self.gh(f"actions/workflows/{WORKFLOW}/runs?per_page=100&page={page}")
            total, rows = value["total_count"], value["workflow_runs"]
            require(
                type(total) is int
                and total >= 0
                and isinstance(rows, list)
                and (count is None or count == total),
                "Workflow run inventory changed",
            )
            count = total
            for row in rows:
                require(row["id"] not in seen, "Duplicate workflow run")
                seen.add(row["id"])
                require(
                    row.get("status") == "completed", "Existing publication workflow is not idle"
                )
            require(len(seen) <= total, "Invalid workflow run inventory")
            if len(seen) == total:
                return
            require(len(rows) == 100, "Incomplete workflow run inventory")
            page += 1

    def set_variable(self, name: str, value: str) -> None:
        require(
            name in ("STATIC_AUTOMATION_ENABLED", "STATIC_APPROVED_SHA"),
            "Unapproved variable mutation",
        )
        self.gh("actions/variables/" + name, "PATCH", {"name": name, "value": value})
        require(
            self.gh("actions/variables/" + name)["value"] == value,
            "Repository variable update could not be confirmed",
        )

    def set_workflow(self, enabled: bool) -> None:
        self.gh("actions/workflows/" + WORKFLOW + ("/enable" if enabled else "/disable"), "PUT")
        require(
            self.workflow()["state"] == ("active" if enabled else "disabled_manually"),
            "Workflow state change could not be confirmed",
        )

    def target(self, variables: dict) -> dict:
        env = {**self.env, "STATIC_CLOUDFLARE_READ_TOKEN": self.env["CLOUDFLARE_API_TOKEN"]}
        client = probe_providers.Client(env)
        path = f"/client/v4/accounts/{variables['CLOUDFLARE_ACCOUNT_ID']}/pages/projects/navmate"
        project = pages_target.read_project(client, path)
        gate.verify_project(project)
        # Wrangler uploads also support Git-linked projects with automatic deploys disabled.
        # Missing/unknown controls cannot establish that Git builds will stay paused.
        source = project.get("source")
        config = source.get("config") if isinstance(source, dict) else None
        require(
            "source" in project
            and (
                source is None
                or (
                    isinstance(source, dict)
                    and source.get("type") == "github"
                    and isinstance(config, dict)
                    and config.get("production_deployments_enabled") is False
                    and config.get("preview_deployment_setting") == "none"
                )
            ),
            "Pages automatic Git deployments are enabled or unknown",
        )
        observation = pages_target.inspect(client, variables["CLOUDFLARE_ACCOUNT_ID"])
        require(
            all(
                observation.get(name) is True
                for name in (
                    "project_matches",
                    "branch_matches",
                    "domain_matches",
                    "pagination_complete",
                    "project_unchanged",
                    "production_in_inventory",
                )
            ),
            "Pages target unknown or changed",
        )
        require(observation.get("in_flight_present") is False, "Pages deployment may be in flight")
        production = observation.get("production") or {}
        require(
            production.get("state") == "success"
            and production.get("stage") == "deploy"
            and production.get("branch_matches") is True
            and production.get("environment") == "production"
            and production.get("uses_functions") is False,
            "Canonical Pages deployment is unsafe",
        )
        return {
            "id": production["id"],
            "commit": production["commit"],
            "project_digest": fingerprint(
                {
                    key: project.get(key)
                    for key in (
                        "id",
                        "name",
                        "production_branch",
                        "domains",
                        "source",
                        "deployment_configs",
                    )
                }
            ),
        }

    def rules_get(self, path: str) -> dict:
        require(
            re.fullmatch(
                r"projects/[a-z][a-z0-9-]{4,62}/(?:releases/cloud\.firestore|"
                r"rulesets/[A-Za-z0-9-]{1,128})",
                path,
            ),
            "Invalid Rules resource scope",
        )
        token = self.env.get("STATIC_GOOGLE_ACCESS_TOKEN")
        require(token, "Existing Google Rules read authentication unavailable")
        request = Request(
            "https://firebaserules.googleapis.com/v1/" + path,
            headers={"Authorization": "Bearer " + token, "Cache-Control": "no-cache"},
        )
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        require(len(raw) <= 8 * 1024 * 1024, "Rules response too large")
        return json.loads(raw)

    def compatibility(self, sha: str, current: str, variables: dict) -> str:
        transition = RULES_TRANSITION_V1227 if self.rules_transition_v1227 else None
        if transition:
            require(
                sha == transition["sha"]
                and current == transition["current_sha"]
                and variables["VITE_FIREBASE_PROJECT_ID"] == transition["project"],
                "Only the pinned navmate-prod v1.22.7 Rules transition is supported",
            )
            require(
                digest(self.rules_source(current).encode("utf-8")) == transition["before_sha256"]
                and digest(self.rules_source(sha).encode("utf-8")) == transition["after_sha256"],
                "Pinned before/after Rules source hash mismatch",
            )
        for path in COMPATIBILITY:
            if transition and path == "firebase/firestore.rules":
                continue  # Both exact sources were checked above; other contracts remain identical.
            require(
                self.git("show", f"{sha}:{path}") == self.git("show", f"{current}:{path}"),
                "Rules/indexes/Project schema changed; separately reviewed migration required",
            )
        project = variables["VITE_FIREBASE_PROJECT_ID"]
        prefix = "projects/" + project
        live = self.rules_get(prefix + "/releases/cloud.firestore")
        name = live["rulesetName"]
        require(name.startswith(prefix + "/rulesets/"), "Ruleset belongs to another project")
        ruleset = self.rules_get(name)
        files = ruleset["source"]["files"]
        require(
            len(files) == 1
            and files[0]["content"].strip() == self.git("show", f"{sha}:firebase/firestore.rules"),
            "Deployed Firestore Rules differ; no automatic Rules deploy",
        )
        if transition:
            require(
                files[0].get("name") == "firebase/firestore.rules"
                and digest(files[0]["content"].encode("utf-8")) == transition["after_sha256"],
                "Pinned candidate Rules must already be deployed; no automatic Rules deploy",
            )
        require(
            self.rules_get(prefix + "/releases/cloud.firestore") == live,
            "Deployed Rules changed during read",
        )
        self.rules_receipt = {
            "release": live,
            "source_sha256": digest(files[0]["content"].encode("utf-8")),
            "transition": transition,
            "deployment": "external; this script only reads Rules",
        }
        receipt = {"release": live, "content": files[0]["content"].strip()}
        if transition:
            receipt["transition"] = transition
        return fingerprint(receipt)

    def snapshot(self, sha: str, current: str, *, paused: bool = False) -> dict:
        require(
            re.fullmatch(r"[0-9a-f]{40}", sha) and re.fullmatch(r"[0-9a-f]{40}", current),
            "Explicit full target and current SHAs required",
        )
        require(
            not self.git("status", "--porcelain", "--untracked-files=all"),
            "Tooling checkout must be clean",
        )
        require(
            self.git("remote", "get-url", "origin")
            in ("https://github.com/" + REPO + ".git", "git@github.com:" + REPO + ".git"),
            "Unexpected repository remote",
        )
        self.git("fetch", "origin", "main")
        require(
            self.git("rev-parse", sha + "^{commit}") == sha, "Target is not the requested commit"
        )
        self.git("merge-base", "--is-ancestor", sha, "origin/main")
        variables = self.variables()
        repo = self.gh("")
        require(
            repo.get("private") is False and repo.get("full_name") == REPO,
            "Expected public repository",
        )
        require(
            variables["STATIC_ORIGIN"] == gate.ORIGIN
            and re.fullmatch(r"[0-9a-f]{32}", variables["CLOUDFLARE_ACCOUNT_ID"])
            and variables["STATIC_APPROVED_SHA"] == current,
            "Origin/account/current approved SHA mismatch",
        )
        require(
            variables["STATIC_AUTOMATION_ENABLED"] in ("true", "false"), "Unknown automation state"
        )
        if self.rules_transition_v1227:
            require(
                variables["STATIC_AUTOMATION_ENABLED"] == "false",
                "Pause recurring publication before the pinned Rules transition",
            )
        require(
            self.env.get("CLOUDFLARE_API_TOKEN"), "Existing Pages upload authentication unavailable"
        )
        config = {key: variables[key] for key in gate.CONFIGURATION}
        require(
            all(value.strip() for value in config.values()), "Existing public configuration missing"
        )
        canonical = release(gate.ORIGIN, current)
        require(canonical.get("configuration") == config, "Canonical public configuration changed")
        workflow = self.workflow()
        require(
            workflow.get("state") == ("disabled_manually" if paused else "active"),
            "Unexpected workflow state",
        )
        if paused:
            require(
                variables["STATIC_AUTOMATION_ENABLED"] == "false",
                "Recurring publication not paused",
            )
        self.idle()
        target = self.target(variables)
        require(target["commit"] == current, "Pages/canonical approved source mismatch")
        rules = self.compatibility(sha, current, variables)
        return {
            "variables": variables,
            "target": target,
            "rules_digest": rules,
            "rules_receipt": self.rules_receipt,
            "tooling_sha": self.git("rev-parse", "HEAD"),
            "version": self.git("show", f"{sha}:VERSION"),
        }

    def build(self, sha: str, snapshot: dict, destination: Path) -> dict:
        destination.mkdir(mode=0o700)
        app, feed = destination / "app", destination / "feed"
        command(["git", "clone", "--no-hardlinks", "--no-checkout", str(self.root), str(app)])
        command(["git", "checkout", "--detach", sha], cwd=app)
        gate.static_sources(app)
        restore(gate.ORIGIN, feed, datetime.now(UTC))
        # App upgrades may reacquire the same Run. The producer actually decodes it;
        # validate_feed rejects retained-only timestamp renewal. MSM-only refresh is unchanged.
        report = json.loads(
            command(
                [
                    sys.executable,
                    str(self.root / "scripts/prepare_msm_feed.py"),
                    "--output",
                    str(feed),
                    "--cache",
                    str(destination / "cache"),
                    "--hours",
                    str(PUBLICATION_GENERATION_HOURS),
                ],
                timeout=1800,
            )
        )
        write_json(destination / "feed-report.json", report)
        validate_feed(feed, report, datetime.now(UTC))
        image = "autonavlog-static-release:" + snapshot["tooling_sha"]
        command(
            ["docker", "build", "-f", "scripts/static_ops/Dockerfile.build", "-t", image, "."],
            cwd=self.root,
            timeout=1800,
        )
        config = {key: snapshot["variables"][key] for key in gate.CONFIGURATION}
        args = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--mount",
            f"type=bind,source={app},target=/app",
            "--mount",
            f"type=bind,source={feed},target=/feed,readonly",
        ]
        for name in gate.CONFIGURATION:
            args += ["--env", name]
        args += [
            "--env",
            "AUTONAVLOG_MSM_FEED=/feed",
            "--env",
            "AUTONAVLOG_REFERENCE_PYTHON=python3",
            image,
            "sh",
            "-eu",
            "-s",
        ]
        # Allowlist the host environment passed to Docker; credentials are never inherited.
        build_env = {
            key: self.env[key] for key in ("PATH", "HOME", "DOCKER_HOST") if key in self.env
        }
        build_env.update(config)
        command(
            args,
            env=build_env,
            input_text=(self.root / "scripts/static_ops/build_app_release.sh").read_text(),
            timeout=2700,
        )
        expected = {"commit": sha, "configuration": config}
        candidate = gate.artifact(app, expected)
        require(candidate["version"] == snapshot["version"], "Candidate version mismatch")
        gate.upload_context(app, destination / "upload", expected)
        self.validate_build(destination, sha, snapshot)
        return candidate

    def validate_build(self, destination: Path, sha: str, snapshot: dict) -> dict:
        stage = destination / "upload"
        gate.verify_context(stage)
        candidate = json.loads((stage / "dist-static/release.json").read_text())
        require(
            candidate["commit"] == sha
            and candidate["version"] == snapshot["version"]
            and candidate["configuration"]
            == {key: snapshot["variables"][key] for key in gate.CONFIGURATION},
            "Reviewed candidate SHA/version/configuration changed",
        )
        report = json.loads((destination / "feed-report.json").read_text())
        validate_feed(stage / "dist-static/weather/msm", report, datetime.now(UTC))
        command(
            ["node", str(self.root / "web/scripts/check-static.mjs"), str(stage / "dist-static")]
        )
        return candidate

    def prepare_upload(self, destination: Path, sha: str, snapshot: dict) -> dict:
        self.validate_build(destination, sha, snapshot)
        # Locked trusted CLI installation; never candidate node_modules or Wrangler config.
        command(["npm", "--prefix", "services/taf-proxy", "ci", "--ignore-scripts"], cwd=self.root)
        env = {
            key: self.env[key]
            for key in ("PATH", "HOME", "CLOUDFLARE_API_TOKEN")
            if key in self.env
        }
        env.update(CI="true", WRANGLER_WRITE_LOGS="false", WRANGLER_SEND_METRICS="false")
        env["CLOUDFLARE_ACCOUNT_ID"] = snapshot["variables"]["CLOUDFLARE_ACCOUNT_ID"]
        unchanged(
            snapshot,
            self.snapshot(sha, snapshot["variables"]["STATIC_APPROVED_SHA"], paused=True),
            deployment=True,
        )
        self.validate_build(destination, sha, snapshot)
        return env

    def upload(self, destination: Path, sha: str, env: dict) -> None:
        # Only this command can change remote production; all prechecks are complete.
        command(
            [
                str(self.wrangler),
                "pages",
                "deploy",
                "dist-static",
                "--project-name",
                "navmate",
                "--branch",
                "main",
                "--commit-hash",
                sha,
            ],
            cwd=destination / "upload",
            env=env,
            timeout=600,
        )

    def verify(self, destination: Path, sha: str, snapshot: dict) -> None:
        env = {**self.env, "STATIC_ORIGIN": gate.ORIGIN, "EXPECTED_SOURCE": sha}
        command(
            [
                sys.executable,
                str(self.root / "scripts/static_ops/wait_for_candidate.py"),
                str(destination / "upload/dist-static/release.json"),
            ],
            env=env,
            timeout=330,
        )
        monitor(gate.ORIGIN, datetime.now(UTC))
        self.verify_serving(destination)
        target = self.target(snapshot["variables"])
        require(
            target["commit"] == sha
            and target["project_digest"] == snapshot["target"]["project_digest"],
            "Published Pages identity/configuration mismatch",
        )

    def verify_serving(self, destination: Path) -> None:
        candidate = json.loads((destination / "upload/dist-static/release.json").read_text())
        opener = build_opener(NoRedirect)
        for name, kind in (
            ("index.html", "text/html"),
            ("local/manifest.json", "application/json"),
        ):
            request = Request(
                gate.ORIGIN + ("/" if name == "index.html" else "/" + name),
                headers={"Cache-Control": "no-cache"},
            )
            with opener.open(request, timeout=30) as response:
                require(
                    response.status == 200
                    and response.headers.get("ETag")
                    and "no-cache" in response.headers.get("Cache-Control", "")
                    and response.headers.get_content_type() == kind,
                    "Canonical serving/header verification failed",
                )
                raw = response.read(8 * 1024 * 1024 + 1)
            require(
                candidate["files"][name] == {"bytes": len(raw), "sha256": digest(raw)},
                "Canonical HTML/manifest content differs from fresh upload",
            )
        missing = gate.ORIGIN + "/local/" + candidate["commit"] + ".missing.whl"
        try:
            with opener.open(Request(missing), timeout=30):
                raise Blocked("Missing canonical asset did not return 404")
        except HTTPError as error:
            require(error.code == 404, "Missing canonical asset did not return 404")


def unchanged(before: dict, after: dict, *, paused: bool = False, deployment: bool = False) -> None:
    expected = json.loads(json.dumps(before))
    if paused:
        expected["variables"]["STATIC_AUTOMATION_ENABLED"] = "false"
    if not deployment:
        expected["target"]["id"] = after["target"][
            "id"
        ]  # Same-source scheduled feed may have advanced.
    require(expected == after, "Reviewed target, configuration, Rules or tooling changed")


def prepare(operator: Operator, sha: str, current: str, store: Path, journal: Journal) -> dict:
    snapshot = operator.snapshot(sha, current)
    journal.value["rules_receipt"] = snapshot.get("rules_receipt")
    journal.save()
    journal.stage("build-test")
    operator.build(sha, snapshot, store / "prepared")
    unchanged(snapshot, operator.snapshot(sha, current))
    plan = {
        "schema_version": 1,
        "sha": sha,
        "current_sha": current,
        "prepared_at": datetime.now(UTC).isoformat(),
        "snapshot": snapshot,
        "release_digest": digest((store / "prepared/upload/dist-static/release.json").read_bytes()),
        "feed_report_digest": digest((store / "prepared/feed-report.json").read_bytes()),
    }
    write_json(store / "plan.json", plan)
    return plan


def publish(
    operator: Operator, sha: str, current: str, store: Path, reviewed: str, journal: Journal
) -> None:
    require(re.fullmatch(r"[0-9a-f]{64}", reviewed), "Explicit reviewed plan SHA-256 required")
    raw = (store / "plan.json").read_bytes()
    require(digest(raw) == reviewed, "Plan differs from explicitly reviewed SHA-256")
    plan = json.loads(raw)
    require(
        plan["schema_version"] == 1 and plan["sha"] == sha and plan["current_sha"] == current,
        "Requested SHA differs from reviewed plan",
    )
    require(
        datetime.now(UTC) - timedelta(minutes=30)
        <= instant(plan["prepared_at"])
        <= datetime.now(UTC),
        "Prepare is stale or in the future; prepare again",
    )
    snapshot = operator.snapshot(sha, current)
    journal.value["rules_receipt"] = snapshot.get("rules_receipt")
    journal.save()
    unchanged(plan["snapshot"], snapshot)
    require(
        digest((store / "prepared/upload/dist-static/release.json").read_bytes())
        == plan["release_digest"]
        and digest((store / "prepared/feed-report.json").read_bytes())
        == plan["feed_report_digest"],
        "Reviewed prepared evidence changed",
    )
    operator.validate_build(store / "prepared", sha, snapshot)
    journal.stage("pause")
    journal.mutation(
        "automation", lambda: operator.set_variable("STATIC_AUTOMATION_ENABLED", "false")
    )
    # Also block new dispatches. Existing queued/running jobs are not canceled.
    journal.mutation("workflow", lambda: operator.set_workflow(False))
    journal.stage("quiescence")
    paused = operator.snapshot(sha, current, paused=True)
    unchanged(snapshot, paused, paused=True)
    journal.value["previous_deployment"] = paused["target"]["id"]
    journal.save()
    journal.stage("fresh-build-test")
    operator.build(sha, paused, store / "fresh")  # Never upload prepare's dist.
    unchanged(paused, operator.snapshot(sha, current, paused=True), deployment=True)
    journal.stage("pre-upload")
    upload_env = operator.prepare_upload(store / "fresh", sha, paused)
    journal.stage("upload")
    journal.mutation("production", lambda: operator.upload(store / "fresh", sha, upload_env))
    journal.stage("canonical-verification")
    operator.verify(store / "fresh", sha, paused)
    # Check config/state again before changing the approved source or resuming.
    expected = {**paused["variables"]}
    require(operator.variables() == expected, "Variables changed after publication")
    require(operator.workflow()["state"] == "disabled_manually", "Workflow resumed unexpectedly")
    require(
        operator.compatibility(sha, current, expected) == paused["rules_digest"],
        "Rules changed after publication",
    )
    operator.idle()
    journal.stage("approved-sha")
    journal.mutation("approved_sha", lambda: operator.set_variable("STATIC_APPROVED_SHA", sha))
    journal.stage("resume")
    expected["STATIC_APPROVED_SHA"] = sha
    require(operator.variables() == expected, "Approved/configuration update mismatch")
    operator.verify(store / "fresh", sha, paused)
    journal.mutation("workflow", lambda: operator.set_workflow(True))
    if snapshot["variables"]["STATIC_AUTOMATION_ENABLED"] == "true":
        journal.mutation(
            "automation", lambda: operator.set_variable("STATIC_AUTOMATION_ENABLED", "true")
        )
    journal.value["final_automation"] = snapshot["variables"]["STATIC_AUTOMATION_ENABLED"]
    journal.value["final_approved_sha"] = sha
    journal.save()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "publish"))
    parser.add_argument(
        "--sha",
        required=True,
        help="Full explicitly selected application commit; no latest default",
    )
    parser.add_argument(
        "--current-sha", required=True, help="Expected currently approved production commit"
    )
    parser.add_argument(
        "--store",
        type=Path,
        required=True,
        help="New private local release directory outside checkout",
    )
    parser.add_argument(
        "--reviewed-plan-sha256", help="Publish authorization must cover this exact prepare plan"
    )
    parser.add_argument(
        "--rules-transition-v1227",
        action="store_true",
        help=(
            "Only the pinned navmate-prod v1.20.1 -> v1.22.7 Rules transition; "
            "Rules must already match"
        ),
    )
    args = parser.parse_args()
    store = args.store.resolve()
    require(
        not store.is_relative_to(ROOT) and not ROOT.is_relative_to(store),
        "Release store must be outside the tooling checkout",
    )
    if args.mode == "prepare":
        store.mkdir(mode=0o700, parents=False, exist_ok=False)
    require(
        store.is_dir() and not args.store.is_symlink() and store.stat().st_mode & 0o077 == 0,
        "Release store must be a private regular directory",
    )
    # Serialize local invocations; cross-host exclusivity needs the operator publication window.
    lock_path = Path(command(["git", "rev-parse", "--git-common-dir"], cwd=ROOT).strip())
    if not lock_path.is_absolute():
        lock_path = ROOT / lock_path
    with (lock_path / "static-app-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = store / (args.mode + "-status.json")
        require(
            not report.exists(),
            "Stage already attempted; inspect status and use a new prepare store",
        )
        journal = Journal(report, args.mode, args.sha)
        success = False
        try:
            operator = Operator(rules_transition_v1227=args.rules_transition_v1227)
            if args.mode == "prepare":
                prepare(operator, args.sha, args.current_sha, store, journal)
                journal.value["plan_sha256"] = digest((store / "plan.json").read_bytes())
            else:
                publish(
                    operator,
                    args.sha,
                    args.current_sha,
                    store,
                    args.reviewed_plan_sha256 or "",
                    journal,
                )
            success = True
        except Blocked as error:
            journal.value["reason"] = str(error)
        except (Exception, KeyboardInterrupt):
            # Native failures may contain credentials; emit only a fixed diagnostic.
            journal.value["reason"] = (
                "Provider/validation failure or interruption; native details withheld"
            )
        finally:
            journal.finish(success)
        return 0 if success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt):
        raise SystemExit(
            "App release blocked before execution; check inputs/store/lock locally"
        ) from None
