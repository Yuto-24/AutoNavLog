"""Fresh native collection -> complete schema-v2 validation -> redacted verdict.

This entrypoint never accepts a report, a report path, or an operator-report fallback.
Provider fragments and normalized billing quantities stay in process memory. Native
adapters that cannot yet establish a required fact keep the gate BLOCKED.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime

try:
    from . import collect_providers as native
    from . import operations, verify_limits
except ImportError:
    import collect_providers as native
    import operations
    import verify_limits


METRICS = frozenset({
    "pages_builds", "workers_requests", "firestore_reads", "firestore_writes",
    "firestore_deletes", "firestore_storage_bytes", "firestore_outbound_bytes",
    "actions_storage_gb_hours", "actions_cache_storage_bytes",
})
PLAN_FIELDS = frozenset({
    "cloudflare", "workers", "firebase", "github_runner", "automatic_billing",
})


def assemble(plans, plan_observations, metrics, scopes, limits, now, *, publication=False):
    """Validate a complete in-memory collection; never renew native timestamps.

    Only native adapters call this boundary in production. Arguments are also useful
    for offline tests of composition; the CLI has no JSON/file/report input surface.
    """
    if set(plans) != PLAN_FIELDS or set(plan_observations) != PLAN_FIELDS:
        raise ValueError("incomplete_plan_collection")
    if set(metrics) != METRICS:
        raise ValueError("incomplete_metric_collection")
    if limits.get("status") != "OK" or limits.get("limits") != verify_limits.LIMITS:
        raise ValueError("unverified_official_limits")
    checks = limits.get("checks", [])
    if (
        len(checks) != len(verify_limits.SOURCES)
        or {check.get("source") for check in checks} != set(verify_limits.SOURCES)
        or any(
            check.get("status") != "verified"
            or check.get("url") != verify_limits.SOURCES.get(check.get("source"))
            or not re.fullmatch(r"[0-9a-f]{64}", check.get("sha256", ""))
            for check in checks
        )
    ):
        raise ValueError("incomplete_official_limits")
    for name, limit in limits["limits"].items():
        if metrics[name].get("limit") != limit:
            raise ValueError("metric_limit_differs_from_verified_source")
    observed = [operations.instant(value) for value in plan_observations.values()]
    # Checking every source prevents a fresh source from hiding a future timestamp.
    if any(value > now for value in observed):
        raise ValueError("future_plan_observation")
    report = {
        "schema_version": 2,
        "plans": dict(plans),
        "plans_observed_at": min(observed).isoformat(),
        "limits_checked_at": limits["limits_checked_at"],
        "scopes": dict(scopes),
        "metrics": metrics,
    }
    return operations.quota(report, now, scopes, publication=publication, collected=True)


def evaluate(env, now, *, client=None, limits_client=None, publication=False):
    """Attempt independent sources and expose fixed statuses only, even on failure."""
    fragments, checks = native.collect_fragments(env, now, client)
    limits = verify_limits.audit(now, limits_client)
    plans = {}
    plan_observations = {}
    identity = fragments.get("firebase_identity_and_spark")
    if identity is not None:
        plans["firebase"] = identity["plan"]
        plan_observations["firebase"] = identity["observed_at"]
    metrics = {name: fragments[name] for name in METRICS if name in fragments}
    cache = fragments.get("github_cache_usage_and_configuration")
    if cache is not None:
        metrics["actions_cache_storage_bytes"] = cache
    scopes = {
        "cloudflare_account": env.get("CLOUDFLARE_ACCOUNT_ID", ""),
        "firebase_project": env.get("VITE_FIREBASE_PROJECT_ID", ""),
        "github_owner": env.get("GITHUB_REPOSITORY_OWNER", ""),
        "github_repository": env.get("GITHUB_REPOSITORY", ""),
    }
    # Reachability, missing counters and net-zero billing are not replacements for
    # these still-unverified native evidence mappings. No fabricated plan/zero is
    # supplied to assemble(), including in publication mode.
    missing = [
        "cloudflare_plans_and_account_coverage",
        "workers_quota_coverage",
        "firestore_outbound_current_audit",
        "github_owner_shared_allowance_and_billing_control",
        "github_cache_accrued_billing",
    ]
    try:
        assemble(plans, plan_observations, metrics, scopes, limits, now, publication=publication)
        status = "OK"
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        status = "BLOCKED"
    return {
        "status": status,
        "gate": "publication" if publication else "monitor",
        "attempted_at": now.isoformat(),
        "collection": checks,
        "official_limits": [
            {"source": check["source"], "status": check["status"]}
            for check in limits["checks"]
        ],
        "unresolved_evidence": missing,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication", action="store_true")
    args = parser.parse_args()
    result = evaluate(os.environ, datetime.now(UTC), publication=args.publication)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "OK" else 1)


if __name__ == "__main__":
    main()
