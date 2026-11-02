"""Bound external heavy work under static-production concurrency; no provider secrets."""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from enum import Enum
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    from .operations import NoRedirect
except ImportError:
    from operations import NoRedirect

PREFIXES = ("msm-fallback/", "msm-request/")

API = "https://api.github.com/repos/Yuto-24/AutoNavLog/actions/workflows/static-production.yml/runs"


class Reason(Enum):
    """Only fixed codes may cross the diagnostic boundary."""
    UNKNOWN_LOCK = "UNKNOWN_LOCK"
    UNKNOWN_LEASE = "UNKNOWN_LEASE"
    LOCK_NOT_HELD = "LOCK_NOT_HELD"
    LOCK_CONFLICT = "LOCK_CONFLICT"
    LOCK_MISSING = "LOCK_MISSING"
    UNKNOWN_ACTIONS_STATUS = "UNKNOWN_ACTIONS_STATUS"
    INVALID_INPUTS = "INVALID_INPUTS"
    INVALID_OBSERVED_RUN = "INVALID_OBSERVED_RUN"
    CURRENT_REQUEST_MISSING = "CURRENT_REQUEST_MISSING"
    DAILY_LIMIT = "DAILY_LIMIT"
    PER_RUN_LIMIT = "PER_RUN_LIMIT"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    ACTIVE_CONFLICT = "ACTIVE_CONFLICT"
    COOLDOWN = "COOLDOWN"
    RESPONSE_TOO_LARGE = "RESPONSE_TOO_LARGE"
    INCOMPLETE_HISTORY = "INCOMPLETE_HISTORY"
    INVALID_DETECTED_RUN = "INVALID_DETECTED_RUN"
    FUTURE_DETECTED_RUN = "FUTURE_DETECTED_RUN"


class GateRejection(ValueError):
    """Keep existing ValueError messages for callers, separate from safe diagnostics."""

    def __init__(self, reason: Reason, message: str):
        super().__init__(message)
        self.reason = reason


def lock_waiters(value: dict, own_id: int) -> set[int]:
    """Require this workflow's live lease and identify only confirmed lock waiters."""
    members = value.get("group_members")
    count = value.get("total_count")
    if (value.get("group_name") != "static-production" or type(count) is not int
            or not 1 <= count <= 100 or not isinstance(members, list) or len(members) != count):
        raise GateRejection(Reason.UNKNOWN_LOCK, "Unknown publication lock")
    seen = set()
    waiters = set()
    for member in members:
        run_id = member.get("run_id")
        if (type(run_id) is not int or run_id <= 0 or run_id in seen
                or member.get("job_id") is not None):
            raise GateRejection(Reason.UNKNOWN_LEASE, "Unknown publication lease")
        seen.add(run_id)
        if run_id == own_id:
            if member.get("status") != "in_progress":
                raise GateRejection(Reason.LOCK_NOT_HELD,
                                    "Current workflow does not hold publication lock")
        elif member.get("status") == "pending":
            waiters.add(run_id)
        else:
            raise GateRejection(Reason.LOCK_CONFLICT, "Another workflow holds publication lock")
    if own_id not in seen:
        raise GateRejection(Reason.LOCK_MISSING, "Current workflow missing from publication lock")
    return waiters


def conflicting_active(runs: list[dict], own_id: int, waiters: set[int]) -> bool:
    """Reject active runs unless the live concurrency API confirms they only wait."""
    for run in runs:
        status = run["status"]
        if status not in ("completed", "queued", "in_progress", "waiting", "pending", "requested"):
            raise GateRejection(Reason.UNKNOWN_ACTIONS_STATUS, "Unknown Actions status")
        if run["id"] != own_id and status != "completed" and run["id"] not in waiters:
            return True
    return False


def validate(event: dict, env: dict, runs: list[dict], now: datetime,
             waiters: set[int] | None = None) -> None:
    """Reject unsafe feed inputs or exhausted Actions budgets after publication serialization."""
    inputs = event.get("inputs", {})
    target = inputs.get("observed_run", "")
    request_id = inputs.get("request_id", "")
    request = bool(request_id)
    if (inputs.get("feed_refresh") not in (True, "true")
            or inputs.get("publish") not in (True, "true") or inputs.get("source_sha")
            or (request and (target or not re.fullmatch(r"\d{13}", request_id)))
            or (not request and not re.fullmatch(r"\d{14}", target))
            or env.get("GITHUB_RUN_ATTEMPT") != "1"):
        raise GateRejection(Reason.INVALID_INPUTS,
                            "External trigger requires feed-only publish, a Run and no rerun")
    if not request:
        observed = datetime.strptime(target, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if observed > now or observed.hour % 3 or observed.minute or observed.second:
            raise GateRejection(Reason.INVALID_OBSERVED_RUN, "Invalid advertised Run")
    own_id = int(env["GITHUB_RUN_ID"])
    own = [run for run in runs if run["id"] == own_id]
    title = "msm-request/" + request_id if request else "msm-fallback/" + target
    if len(own) != 1 or own[0]["display_title"] != title:
        raise GateRejection(Reason.CURRENT_REQUEST_MISSING,
                            "Current external request missing from Actions history")
    created = datetime.fromisoformat(own[0]["created_at"].replace("Z", "+00:00"))
    # Include queued requests conservatively; unknown/truncated history never grants budget.
    others = [run for run in runs if run["id"] != own_id
              and run["display_title"].startswith(PREFIXES)]
    if sum(run["created_at"][:10] == created.date().isoformat() for run in others) >= 8:
        raise GateRejection(Reason.DAILY_LIMIT, "External daily heavy-work limit reached")
    if not request and sum(run["display_title"] == "msm-fallback/" + target for run in others) >= 2:
        raise GateRejection(Reason.PER_RUN_LIMIT, "External per-Run heavy-work limit reached")
    if request and any(run["display_title"] == "msm-request/" + request_id for run in others):
        raise GateRejection(Reason.DUPLICATE_REQUEST, "Duplicate external request")
    if conflicting_active(runs, own_id, waiters or set()):
        raise GateRejection(Reason.ACTIVE_CONFLICT,
                            "Another publication is active or awaiting approval")
    for run in others:
        timestamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if timestamp <= created and created - timestamp < timedelta(minutes=90):
            raise GateRejection(Reason.COOLDOWN, "External heavy-work cooldown")


def history(query: dict) -> list[dict]:
    """Read complete bounded Actions evidence without redirects or response logging."""
    request = Request(API + "?" + urlencode({"per_page": 100, **query}), headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
    })
    return read_list(request, "workflow_runs", "total_count")


def read_json(request: Request) -> dict:
    """Read one bounded JSON response without redirects or provider-body logging."""
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise GateRejection(Reason.RESPONSE_TOO_LARGE, "History exceeds budget")
    return json.loads(raw)


def read_list(request: Request, key: str, count: str) -> list[dict]:
    """Reject truncated, duplicate or oversized evidence rather than granting a budget."""
    value = read_json(request)
    rows = value[key]
    if (type(value[count]) is not int or not 0 <= value[count] <= 100
            or len(rows) != value[count] or len({row["id"] for row in rows}) != len(rows)):
        raise GateRejection(Reason.INCOMPLETE_HISTORY, "Incomplete Actions history")
    return rows


def detected_limit(target: str, runs: list[dict], own_id: int, jobs) -> None:
    """Count actual discovered Runs in durable Actions job names, including failed attempts."""
    observed = datetime.strptime(target, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if observed.hour % 3 or observed.minute or observed.second:
        raise GateRejection(Reason.INVALID_DETECTED_RUN, "Invalid detected Run")
    attempts = 0
    for run in runs:
        if run["id"] == own_id or not run["display_title"].startswith(PREFIXES):
            continue
        if run["display_title"] == "msm-fallback/" + target:
            attempts += 1
        elif any(job["name"] == "msm-detected/" + target for job in jobs(run["id"])):
            attempts += 1
    if attempts >= 2:
        raise GateRejection(Reason.PER_RUN_LIMIT, "External per-Run heavy-work limit reached")


def main() -> None:
    """Recheck budgets and active/approval states inside shared publication concurrency."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detected-run")
    args = parser.parse_args()
    stage = "event"
    try:
        env = os.environ
        event = json.loads(open(env["GITHUB_EVENT_PATH"]).read())
        now = datetime.now(UTC)
        # Include the entire request day even after a long approval wait.
        stage = "current_run"
        own_request = Request(API.rsplit("/workflows/", 1)[0] + "/runs/" + env["GITHUB_RUN_ID"],
                              headers={"Authorization": "Bearer " + env["GH_TOKEN"]})
        with build_opener(NoRedirect).open(own_request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise GateRejection(Reason.RESPONSE_TOO_LARGE, "Oversize current run")
        own_start = datetime.fromisoformat(json.loads(raw)["created_at"].replace("Z", "+00:00"))
        since = min(own_start.replace(hour=0, minute=0, second=0, microsecond=0),
                    now - timedelta(days=1))
        own_id = int(env["GITHUB_RUN_ID"])
        lock_request = Request(API.rsplit("/workflows/", 1)[0]
                               + "/concurrency_groups/static-production", headers={
                                   "Authorization": "Bearer " + env["GH_TOKEN"],
                                   "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2026-03-10",
                               })
        stage = "created_history"
        runs = history({"created": ">=" + since.isoformat()})
        active = []
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            stage = "active_" + status
            active.extend(history({"status": status}))
        # Read the live queue last so a newly observed waiter is not compared
        # against an older queue snapshot.
        stage = "live_concurrency"
        lock = read_json(lock_request)
        stage = "validate_lock"
        waiters = lock_waiters(lock, own_id)
        stage = "validate_active"
        if conflicting_active(active, own_id, waiters):
            raise GateRejection(Reason.ACTIVE_CONFLICT,
                            "Another publication is active or awaiting approval")
        stage = "validate"
        validate(event, env, runs, now, waiters)
        if args.detected_run:
            def jobs(run_id):
                nonlocal stage
                stage = "detected_jobs"
                url = API.rsplit("/workflows/", 1)[0] + f"/runs/{run_id}/jobs?per_page=100"
                request = Request(url,
                                  headers={"Authorization": "Bearer " + env["GH_TOKEN"]})
                rows = read_list(request, "jobs", "total_count")
                stage = "detected_limit"
                return rows
            stage = "detected_run"
            detected = datetime.strptime(args.detected_run, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            if detected > now:
                raise GateRejection(Reason.FUTURE_DETECTED_RUN, "Future detected Run")
            # Per-Run caps do not reset with the daily/24-hour request window.
            stage = "detected_history"
            run_history = history({"created": ">=" + min(since, detected).isoformat()})
            stage = "detected_limit"
            detected_limit(args.detected_run, run_history, int(env["GITHUB_RUN_ID"]), jobs)
    except Exception as error:
        # Never format exceptions, URLs, headers, response bodies, or event values.
        reason = "UNEXPECTED_ERROR"
        http_status = ""
        if isinstance(error, GateRejection) and type(error.reason) is Reason:
            reason = error.reason.value
        elif isinstance(error, HTTPError):
            reason = "HTTP_ERROR"
            if type(error.code) is int and 100 <= error.code <= 599:
                http_status = f" http_status={error.code}"
        raise SystemExit(
            "External trigger gate blocked; check inputs, Actions availability and limits"
            f"; stage={stage} reason={reason}{http_status}"
        ) from None
    print('{"status":"EXTERNAL_BUDGET_OK"}')


if __name__ == "__main__":
    main()
