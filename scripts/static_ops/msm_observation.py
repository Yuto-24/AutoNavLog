"""Pure observation checks: receipts identify Actions; CPU evidence never proves publication."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

REPOSITORY = "Yuto-24/AutoNavLog"
CPU_LIMIT_US = 10_000


def correlate_dispatch(tick: dict, run: dict, version: str, commit: str) -> dict:
    """Bind the natural Cron receipt to an Actions run without assuming queue latency."""
    receipts = [r for r in tick.get("results", []) if r.get("status") == "DISPATCH_ACCEPTED"]
    if (
        len(receipts) != 1
        or tick.get("version_id") != version
        or tick.get("cron") != "*/15 * * * *"
    ):
        raise ValueError("Dispatch receipt unavailable or wrong deployment")
    receipt = receipts[0]
    request_id = receipt.get("request_id")
    run_id = receipt.get("workflow_run_id")
    if (
        not isinstance(request_id, str)
        or not re.fullmatch(r"\d{13}", request_id)
        or type(run_id) is not int
        or not 0 < run_id <= 2**53 - 1
        or receipt.get("publication") != "NOT_VERIFIED"
        or receipt.get("run_url")
        != f"https://api.github.com/repos/{REPOSITORY}/actions/runs/{run_id}"
        or receipt.get("html_url") != f"https://github.com/{REPOSITORY}/actions/runs/{run_id}"
        or not any(
            d.get("stage") == "dispatch" and d.get("status") == 200
            for d in tick.get("diagnostics", [])
        )
    ):
        raise ValueError("Invalid dispatch receipt")
    if (
        run.get("id") != run_id
        or run.get("display_title") != "msm-request/" + request_id
        or run.get("event") != "workflow_dispatch"
        or run.get("head_sha") != commit
        or run.get("run_attempt") != 1
    ):
        raise ValueError("Actions run does not match receipt")
    return {k: receipt[k] for k in ("request_id", "workflow_run_id", "run_url", "html_url")}


def cpu_window(tick: dict, receipt: dict) -> tuple[str, str]:
    """Cover enqueue and execution timestamps; provider request count must disambiguate it."""
    points = [tick.get("eventTimestamp"), tick.get("scheduled_time")]
    request_id = receipt.get("request_id")
    if not isinstance(request_id, str) or not re.fullmatch(r"\d{13}", request_id):
        raise ValueError("Invalid request timestamp")
    points.append(int(request_id))
    wall = tick.get("wallTime")
    if any(type(p) not in (int, float) or not math.isfinite(p) or p <= 0 for p in points):
        raise ValueError("Missing observation timestamp")
    if type(wall) not in (int, float) or not math.isfinite(wall) or wall < 0:
        raise ValueError("Missing wall time")
    start = datetime.fromtimestamp((min(points) - 5000) / 1000, UTC)
    end = datetime.fromtimestamp((max(points) + wall + 60_000) / 1000, UTC)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def assess_cpu(tick: dict, response: dict) -> dict:
    """Keep observer UNKNOWN separate from execution failure and proven CPU excess."""
    outcome = tick.get("outcome")
    if isinstance(outcome, str) and outcome not in ("ok", "unknown"):
        return {"status": "WORKER_FAILED"}
    tail = tick.get("cpuTime")
    if type(tail) in (int, float) and math.isfinite(tail) and tail > 10:
        return {"status": "OVER_LIMIT", "tail_ms": tail}
    if not isinstance(response, dict):
        return {"status": "UNKNOWN"}
    try:
        if response.get("errors"):
            return {"status": "UNKNOWN"}
        accounts = response["data"]["viewer"]["accounts"]
        if len(accounts) != 1:
            return {"status": "UNKNOWN"}
        rows = accounts[0]["workersInvocationsAdaptive"]
        if not isinstance(rows, list) or not rows:
            return {"status": "UNKNOWN"}
        count = 0
        for row in rows:
            requests, cpu = row["sum"]["requests"], row["max"]["cpuTime"]
            if (
                type(requests) is not int
                or requests < 0
                or type(cpu) not in (int, float)
                or not math.isfinite(cpu)
                or cpu < 0
            ):
                return {"status": "UNKNOWN"}
            count += requests
            if requests and cpu > CPU_LIMIT_US:
                return {"status": "OVER_LIMIT", "provider_max_us": cpu}
        if outcome != "ok" or count != 1 or len(rows) != 1 or rows[0]["max"]["cpuTime"] <= 0:
            return {"status": "UNKNOWN"}
        return {"status": "WITHIN_LIMIT", "provider_max_us": rows[0]["max"]["cpuTime"]}
    except (KeyError, TypeError, IndexError):
        return {"status": "UNKNOWN"}
