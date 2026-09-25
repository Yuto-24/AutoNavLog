"""Static publishing preflight and read-only monitoring; never mutates a provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Static origin redirected")


def fetch(url: str, limit: int = 25 * 1024 * 1024) -> bytes:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Expected credential-free HTTPS URL")
    with build_opener(NoRedirect).open(
        Request(
            url,
            headers={"Cache-Control": "no-cache", "User-Agent": "AutoNavLog-StaticOperations/1.0"},
        ),
        timeout=60,
    ) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Response exceeds byte budget")
    return data


def instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result


def quota(report: dict, now: datetime, scopes: dict, *, publication: bool = False) -> dict:
    """Unknown, stale, partial and near-limit observations fail closed, never become zero."""
    required = {
        "pages_builds",
        "workers_requests",
        "firestore_reads",
        "firestore_writes",
        "firestore_deletes",
        "firestore_storage_bytes",
        "firestore_outbound_bytes",
        "actions_storage_bytes",
    }
    if report["plans"] != {
        "cloudflare": "Free",
        "workers": "Free",
        "firebase": "Spark",
        "github_runner": "public-standard",
        "automatic_billing": False,
    }:
        raise ValueError("Free-only plan / disabled billing evidence required")
    checked = instant(report["limits_checked_at"])
    if not now - timedelta(days=31) <= checked <= now:
        raise ValueError("Recheck current official limits at least monthly and at release")
    if report["scopes"] != scopes or not all(scopes.values()):
        raise ValueError("Plan evidence differs from deployment target scopes")
    if not now - timedelta(hours=36) <= instant(report["plans_observed_at"]) <= now:
        raise ValueError("Plan/billing evidence is stale or future")
    if publication:
        # Direct Upload consumes no Pages builds; this job stores no Actions artifacts.
        # Optional TAF/Sync exhaustion must not cause an unrelated MSM feed outage.
        return {"status": "OK", "gate": "free-plans-only", "checked_at": now.isoformat()}
    metrics = report["metrics"]
    if set(metrics) != required:
        raise ValueError("Incomplete account-shared quota metrics")
    for name, metric in metrics.items():
        observed = instant(metric["observed_at"])
        if not now - timedelta(hours=36) <= observed <= now:
            raise ValueError(f"Stale/future quota observation: {name}")
        for key in ("used", "limit"):
            value = metric[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
            ):
                raise ValueError(f"Unknown/non-finite {key}: {name}")
        if metric["used"] < 0 or metric["limit"] <= 0:
            raise ValueError(f"Invalid quota value: {name}")
        if not all(metric.get(key) for key in ("scope", "window", "source")):
            raise ValueError(f"Missing quota provenance: {name}")
        provider = (
            "cloudflare_account"
            if name.startswith(("pages_", "workers_"))
            else "firebase_project"
            if name.startswith("firestore_")
            else "github_owner"
        )
        if not scopes.get(provider) or metric["scope"] != scopes[provider]:
            raise ValueError(f"Quota scope differs from deployment target: {name}")
        if name.endswith("storage_bytes"):
            if metric["window"] != "instant":
                raise ValueError(f"Storage requires an instant observation: {name}")
        else:
            zone = ZoneInfo("America/Los_Angeles") if provider == "firebase_project" else UTC
            local = now.astimezone(zone)
            start = local.replace(hour=0, minute=0, second=0, microsecond=0)
            monthly = name in {"pages_builds", "firestore_outbound_bytes"}
            if monthly:
                start = start.replace(day=1)
                end = (start + timedelta(days=32)).replace(day=1)
            else:
                end = start + timedelta(days=1)
            window = metric["window"]
            if not isinstance(window, dict) or (
                instant(window["start"]) != start or instant(window["end"]) != end
            ):
                raise ValueError(f"Quota window does not match current provider period: {name}")
            if not start <= observed < end:
                raise ValueError(f"Observation belongs to another quota period: {name}")
        if metric["used"] / metric["limit"] >= 0.8:
            raise ValueError(f"Quota warning >=80%, publication paused: {name}")
    return {"status": "OK", "observations": len(metrics), "checked_at": now.isoformat()}


def release(origin: str, sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Approved source must be a full commit SHA")
    value = json.loads(fetch(origin.rstrip("/") + "/release.json", 8 * 1024 * 1024))
    if value["commit"] != sha or value["dirty"] is not False:
        raise ValueError(
            "Canonical production differs from approved source; stop rather than undo rollback"
        )
    return value


def restore(origin: str, target: Path, now: datetime) -> dict:
    """Restore retained immutable assets from canonical production, not an evictable CI cache."""
    base = origin.rstrip("/") + "/weather/msm/"
    raw = fetch(base + "catalog.json", 8 * 1024 * 1024)
    catalog = json.loads(raw)
    if catalog["schema_version"] != 1 or not catalog["assets"]:
        raise ValueError("Invalid production catalog")
    # Expired catalog may seed retention, but never passes publication or monitoring.
    target.mkdir(parents=True, exist_ok=True)
    retained = []
    for asset in catalog["assets"]:
        if not re.fullmatch(r"[0-9a-f]{64}\.npz", asset["file"]):
            raise ValueError("Invalid content-addressed asset path")
        if datetime.strptime(asset["run"], "%Y%m%d%H%M%S").replace(tzinfo=UTC) < now - timedelta(
            days=7
        ):
            continue
        data = fetch(base + asset["file"])
        if len(data) != asset["bytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("Retained payload integrity failure")
        (target / asset["file"]).write_bytes(data)
        retained.append(asset)
    catalog["assets"] = retained
    (target / "catalog.json").write_text(json.dumps(catalog))
    return {"retained_assets": len(retained)}


def monitor(origin: str, now: datetime) -> dict:
    catalog = json.loads(fetch(origin.rstrip("/") + "/weather/msm/catalog.json", 8 * 1024 * 1024))
    remaining = (instant(catalog["expires_at"]) - now).total_seconds()
    if not catalog["assets"] or remaining < 7200:
        raise ValueError("MSM freshness warning: less than two hours remain (or expired)")
    if instant(catalog["generated_at"]) > now:
        raise ValueError("MSM catalog generated in future")
    return {"status": "OK", "weather_seconds_remaining": remaining}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["quota", "monitor", "restore", "preflight"])
    parser.add_argument("--publication", action="store_true")
    parser.add_argument("--origin")
    parser.add_argument("--sha")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = datetime.now(UTC)
    if args.command == "quota":
        result = quota(
            json.loads(os.environ["STATIC_QUOTA_REPORT"]),
            now,
            {
                "cloudflare_account": os.environ["CLOUDFLARE_ACCOUNT_ID"],
                "firebase_project": os.environ["VITE_FIREBASE_PROJECT_ID"],
                "github_owner": os.environ["GITHUB_REPOSITORY_OWNER"],
            },
            publication=args.publication,
        )
    elif args.command == "monitor":
        result = monitor(args.origin, now)
    elif args.command == "restore":
        result = restore(args.origin, args.output, now)
    else:
        current = release(args.origin, args.sha)
        result = {"commit": current["commit"], "version": current["version"]}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
