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


def evidence_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.startswith("REPLACE_"):
        raise ValueError(f"Missing/invalid evidence: {label}")
    return value


def evidence_number(value: object, label: str) -> float | int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"Unknown/non-finite/negative value: {label}")
    return value


def quota_window(name: str, metric: dict, now: datetime, observed: datetime) -> None:
    if name.endswith("storage_bytes"):
        if metric["window"] != "instant":
            raise ValueError(f"Storage requires an instant observation: {name}")
        return
    zone = ZoneInfo("America/Los_Angeles") if name.startswith("firestore_") else UTC
    start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    if name in {
        "pages_builds",
        "firestore_outbound_bytes",
        "actions_storage_gb_hours",
        "actions_cache_billing",
    }:
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


def quota(report: dict, now: datetime, scopes: dict, *, publication: bool = False) -> dict:
    """Validate provider evidence; only Spark outbound has an unobservable variant."""
    try:
        return validate_quota(report, now, scopes, publication=publication)
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError(
            "Malformed quota evidence; see docs/static_operations.md (schema v2)"
        ) from error


def validate_quota(report: dict, now: datetime, scopes: dict, *, publication: bool) -> dict:
    if (
        report["plans"]
        != {
            "cloudflare": "Free",
            "workers": "Free",
            "firebase": "Spark",
            "github_runner": "public-standard",
            "automatic_billing": False,
        }
        or report["plans"]["automatic_billing"] is not False
    ):
        raise ValueError("Free-only plan / disabled billing evidence required")
    checked = instant(report["limits_checked_at"])
    if not now - timedelta(days=31) <= checked <= now:
        raise ValueError("Recheck current official limits at least monthly and at release")
    # Publication still accepts the original three-scope plan-only report. The new
    # repository scope belongs to cache monitoring, not the Direct Upload gate.
    plan_scopes = {"cloudflare_account", "firebase_project", "github_owner"}
    reported_scopes = report["scopes"]
    if not isinstance(reported_scopes, dict) or set(reported_scopes) not in (
        plan_scopes,
        plan_scopes | {"github_repository"},
    ):
        raise ValueError("Plan evidence differs from deployment target scopes")
    for key in reported_scopes:
        evidence_text(scopes.get(key), key)
        if reported_scopes[key] != scopes[key]:
            raise ValueError("Plan evidence differs from deployment target scopes")
    if not now - timedelta(hours=36) <= instant(report["plans_observed_at"]) <= now:
        raise ValueError("Plan/billing evidence is stale or future")
    if publication:
        # Optional TAF/Sync exhaustion must not cause an unrelated MSM feed outage.
        return {"status": "OK", "gate": "free-plans-only", "checked_at": now.isoformat()}
    if type(report.get("schema_version")) is not int or report["schema_version"] != 2:
        raise ValueError(
            "Quota schema_version 2 required: replace actions_storage_bytes with provider-native "
            "billing and separate cache evidence; see docs/static_operations.md#migration"
        )
    if set(reported_scopes) != plan_scopes | {"github_repository"}:
        raise ValueError("Quota v2 requires github_repository scope for separate cache evidence")
    if not re.fullmatch(
        re.escape(scopes["github_owner"]) + r"/[^/\s]+", scopes["github_repository"]
    ):
        raise ValueError("GitHub repository scope must belong to the target owner")
    required = {
        "pages_builds",
        "workers_requests",
        "firestore_reads",
        "firestore_writes",
        "firestore_deletes",
        "firestore_storage_bytes",
        "firestore_outbound_bytes",
        "actions_storage_gb_hours",
        "actions_cache_storage_bytes",
    }
    metrics = report["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != required:
        raise ValueError("Incomplete/unexpected quota metrics; migrate to quota schema v2")
    unobservable = []
    for name, metric in metrics.items():
        observed = instant(metric["observed_at"])
        if not now - timedelta(hours=36) <= observed <= now:
            raise ValueError(f"Stale/future quota observation: {name}")
        evidence_text(metric["source"], f"{name}.source")
        provider = (
            "cloudflare_account"
            if name.startswith(("pages_", "workers_"))
            else "firebase_project"
            if name.startswith("firestore_")
            else "github_repository"
            if name == "actions_cache_storage_bytes"
            else "github_owner"
        )
        if metric["scope"] != scopes[provider]:
            raise ValueError(f"Quota scope differs from deployment target: {name}")
        quota_window(name, metric, now, observed)
        observation = metric["observation"]
        common = {"observation", "observed_at", "scope", "window", "source", "unit"}
        if name == "actions_storage_gb_hours":
            if set(metric) != common | {"used", "billed_amount_usd", "shared_allowance"}:
                raise ValueError(
                    "Actions accrued billing fields required; instant bytes are invalid"
                )
            if observation != "accrued_billing" or metric["unit"] != "GB-hours":
                raise ValueError(
                    "Actions storage requires provider accrued GB-hours billing evidence"
                )
            evidence_number(metric["used"], name)
            if evidence_number(metric["billed_amount_usd"], name) != 0:
                raise ValueError("Actions storage has a nonzero billed amount")
            allowance = metric["shared_allowance"]
            if (
                not isinstance(allowance, dict)
                or set(allowance) != {"coverage", "status", "billed_amount_usd", "source"}
                or allowance["coverage"] != "actions_artifacts_and_packages"
                or allowance["status"] != "within_included"
                or evidence_number(allowance["billed_amount_usd"], name) != 0
            ):
                raise ValueError(
                    "Current artifact/Packages shared free allowance evidence required"
                )
            evidence_text(allowance["source"], f"{name}.shared_allowance.source")
            continue
        unit = "bytes" if name.endswith("_bytes") else "count"
        if metric["unit"] != unit:
            raise ValueError(f"Wrong quota unit: {name}")
        limit = evidence_number(metric["limit"], f"{name}.limit")
        if limit <= 0:
            raise ValueError(f"Invalid quota limit: {name}")
        if name == "firestore_outbound_bytes" and observation == "provider_unobservable":
            if (
                set(metric) != common | {"used", "limit", "reason"}
                or metric["used"] is not None
                or metric["reason"] != "spark_no_usage_counter"
                or limit != 10 * 1024**3
            ):
                raise ValueError("Spark outbound requires null usage, 10 GiB free quota and reason")
            unobservable.append(name)
            continue
        extra = (
            {"configured_limit", "configuration_source", "billing"}
            if provider == "github_repository"
            else set()
        )
        if observation != "counter" or set(metric) != common | {"used", "limit"} | extra:
            raise ValueError(f"Numeric counter evidence required: {name}")
        used = evidence_number(metric["used"], f"{name}.used")
        if extra:
            configured = evidence_number(metric["configured_limit"], f"{name}.configured_limit")
            evidence_text(metric["configuration_source"], f"{name}.configuration_source")
            # Provider free capacity, not an operator-selected paid cache limit.
            if limit != 10 * 1024**3 or not 0 < configured <= limit:
                raise ValueError("Cache storage configuration must not exceed its free allowance")
            billing = metric["billing"]
            if not isinstance(billing, dict) or set(billing) != {
                "window",
                "source",
                "billed_amount_usd",
            }:
                raise ValueError("Separate current-month cache billing evidence required")
            evidence_text(billing["source"], f"{name}.billing.source")
            quota_window("actions_cache_billing", billing, now, observed)
            if evidence_number(billing["billed_amount_usd"], f"{name}.billing") != 0:
                raise ValueError("Cache storage has a nonzero billed amount")
        if used / limit >= 0.8:
            raise ValueError(f"Quota warning >=80%: {name}")
    return {
        "status": "OK",
        "observations": len(metrics),
        "checked_at": now.isoformat(),
        "unobservable": unobservable,
        "actions_storage_evaluation": "accrued_billing_and_shared_allowance",
    }


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
                "github_repository": os.environ.get("GITHUB_REPOSITORY", ""),
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
    try:
        main()
    except (ValueError, KeyError) as error:
        raise SystemExit(f"Static operations failed: {error}") from None
