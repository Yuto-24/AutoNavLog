"""Bound external heavy work under static-production concurrency; no provider secrets."""

import json
import os
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, build_opener

try:
    from .operations import NoRedirect
except ImportError:
    from operations import NoRedirect

PREFIXES = ("msm-fallback/", "msm-request/")

API = "https://api.github.com/repos/Yuto-24/AutoNavLog/actions/workflows/static-production.yml/runs"


def validate(event: dict, env: dict, runs: list[dict], now: datetime) -> None:
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
        raise ValueError("External trigger requires feed-only publish, a Run and no rerun")
    if not request:
        observed = datetime.strptime(target, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        if observed > now or observed.hour % 3 or observed.minute or observed.second:
            raise ValueError("Invalid advertised Run")
    own_id = int(env["GITHUB_RUN_ID"])
    own = [run for run in runs if run["id"] == own_id]
    title = "msm-request/" + request_id if request else "msm-fallback/" + target
    if len(own) != 1 or own[0]["display_title"] != title:
        raise ValueError("Current external request missing from Actions history")
    created = datetime.fromisoformat(own[0]["created_at"].replace("Z", "+00:00"))
    # Include queued requests conservatively; unknown/truncated history never grants budget.
    others = [run for run in runs if run["id"] != own_id
              and run["display_title"].startswith(PREFIXES)]
    if sum(run["created_at"][:10] == created.date().isoformat() for run in others) >= 8:
        raise ValueError("External daily heavy-work limit reached")
    if not request and sum(run["display_title"] == "msm-fallback/" + target for run in others) >= 2:
        raise ValueError("External per-Run heavy-work limit reached")
    if request and any(run["display_title"] == "msm-request/" + request_id for run in others):
        raise ValueError("Duplicate external request")
    for run in runs:
        if run["id"] != own_id and run.get("status", "completed") != "completed":
            raise ValueError("Another publication is active or awaiting approval")
    for run in others:
        timestamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if timestamp <= created and created - timestamp < timedelta(minutes=90):
            raise ValueError("External heavy-work cooldown")


def history(query: dict) -> list[dict]:
    """Read complete bounded Actions evidence without redirects or response logging."""
    request = Request(API + "?" + urlencode({"per_page": 100, **query}), headers={
        "Authorization": "Bearer " + os.environ["GH_TOKEN"],
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
    })
    return read_list(request, "workflow_runs", "total_count")


def read_list(request: Request, key: str, count: str) -> list[dict]:
    """Reject truncated, duplicate or oversized evidence rather than granting a budget."""
    with build_opener(NoRedirect).open(request, timeout=30) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("History exceeds budget")
    value = json.loads(raw)
    rows = value[key]
    if (type(value[count]) is not int or not 0 <= value[count] <= 100
            or len(rows) != value[count] or len({row["id"] for row in rows}) != len(rows)):
        raise ValueError("Incomplete Actions history")
    return rows


def detected_limit(target: str, runs: list[dict], own_id: int, jobs) -> None:
    """Count actual discovered Runs in durable Actions job names, including failed attempts."""
    observed = datetime.strptime(target, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if observed.hour % 3 or observed.minute or observed.second:
        raise ValueError("Invalid detected Run")
    attempts = 0
    for run in runs:
        if run["id"] == own_id or not run["display_title"].startswith(PREFIXES):
            continue
        if run["display_title"] == "msm-fallback/" + target:
            attempts += 1
        elif any(job["name"] == "msm-detected/" + target for job in jobs(run["id"])):
            attempts += 1
    if attempts >= 2:
        raise ValueError("External per-Run heavy-work limit reached")


def main() -> None:
    """Recheck budgets and active/approval states inside shared publication concurrency."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detected-run")
    args = parser.parse_args()
    try:
        env = os.environ
        event = json.loads(open(env["GITHUB_EVENT_PATH"]).read())
        now = datetime.now(UTC)
        # Include the entire request day even after a long approval wait.
        own_request = Request(API.rsplit("/workflows/", 1)[0] + "/runs/" + env["GITHUB_RUN_ID"],
                              headers={"Authorization": "Bearer " + env["GH_TOKEN"]})
        with build_opener(NoRedirect).open(own_request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Oversize current run")
        own_start = datetime.fromisoformat(json.loads(raw)["created_at"].replace("Z", "+00:00"))
        since = min(own_start.replace(hour=0, minute=0, second=0, microsecond=0),
                    now - timedelta(days=1))
        runs = history({"created": ">=" + since.isoformat()})
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            if any(run["id"] != int(env["GITHUB_RUN_ID"]) for run in history({"status": status})):
                raise ValueError("Another publication is active or awaiting approval")
        validate(event, env, runs, now)
        if args.detected_run:
            def jobs(run_id):
                url = API.rsplit("/workflows/", 1)[0] + f"/runs/{run_id}/jobs?per_page=100"
                request = Request(url,
                                  headers={"Authorization": "Bearer " + env["GH_TOKEN"]})
                return read_list(request, "jobs", "total_count")
            detected = datetime.strptime(args.detected_run, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            if detected > now:
                raise ValueError("Future detected Run")
            # Per-Run caps do not reset with the daily/24-hour request window.
            run_history = history({"created": ">=" + min(since, detected).isoformat()})
            detected_limit(args.detected_run, run_history, int(env["GITHUB_RUN_ID"]), jobs)
    except Exception:
        raise SystemExit(
            "External trigger gate blocked; check inputs, Actions availability and limits"
        ) from None
    print('{"status":"EXTERNAL_BUDGET_OK"}')


if __name__ == "__main__":
    main()
