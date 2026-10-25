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

API = "https://api.github.com/repos/Yuto-24/AutoNavLog/actions/workflows/static-production.yml/runs"


def validate(event: dict, env: dict, runs: list[dict], now: datetime) -> None:
    """Reject unsafe feed inputs or exhausted Actions budgets after publication serialization."""
    inputs = event.get("inputs", {})
    target = inputs.get("observed_run", "")
    if (inputs.get("feed_refresh") not in (True, "true")
            or inputs.get("publish") not in (True, "true") or inputs.get("source_sha")
            or not re.fullmatch(r"\d{14}", target)
            or env.get("GITHUB_RUN_ATTEMPT") != "1"):
        raise ValueError("External trigger requires feed-only publish, a Run and no rerun")
    observed = datetime.strptime(target, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    if observed > now or observed.hour % 3 or observed.minute or observed.second:
        raise ValueError("Invalid advertised Run")
    own_id = int(env["GITHUB_RUN_ID"])
    own = [run for run in runs if run["id"] == own_id]
    if len(own) != 1 or own[0]["display_title"] != "msm-fallback/" + target:
        raise ValueError("Current external request missing from Actions history")
    created = datetime.fromisoformat(own[0]["created_at"].replace("Z", "+00:00"))
    # Include queued requests conservatively; unknown/truncated history never grants budget.
    others = [run for run in runs if run["id"] != own_id
              and run["display_title"].startswith("msm-fallback/")]
    if sum(run["created_at"][:10] == created.date().isoformat() for run in others) >= 8:
        raise ValueError("External daily heavy-work limit reached")
    if sum(run["display_title"] == "msm-fallback/" + target for run in others) >= 2:
        raise ValueError("External per-Run heavy-work limit reached")
    for run in others:
        timestamp = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if timestamp <= created and created - timestamp < timedelta(minutes=90):
            raise ValueError("External heavy-work cooldown")


def main() -> None:
    """Fetch bounded Actions history and fail closed without exposing provider response details."""
    try:
        env = os.environ
        event = json.loads(open(env["GITHUB_EVENT_PATH"]).read())
        now = datetime.now(UTC)
        query = urlencode({
            "per_page": 100, "created": ">=" + (now - timedelta(days=1)).isoformat(),
        })
        request = Request(API + "?" + query, headers={
            "Authorization": "Bearer " + env["GH_TOKEN"],
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
        })
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("History exceeds budget")
        history = json.loads(raw)
        runs = history["workflow_runs"]
        if (type(history["total_count"]) is not int
                or not 1 <= history["total_count"] <= 100
                or len(runs) != history["total_count"]):
            raise ValueError("Incomplete Actions history")
        validate(event, env, runs, now)
    except Exception:
        raise SystemExit(
            "External trigger gate blocked; check inputs, Actions availability and limits"
        ) from None
    print('{"status":"EXTERNAL_BUDGET_OK"}')


if __name__ == "__main__":
    main()
