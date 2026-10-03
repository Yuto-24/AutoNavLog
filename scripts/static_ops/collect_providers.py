"""Collect verified native evidence fragments; unsupported evidence fails closed.

No report input, persistent cache, raw export, or publication authorization. The CLI
only exposes fixed status codes. Partial fragments remain in memory, never artifacts.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from . import probe_providers as api
except ImportError:  # Direct script execution in the workflow checkout.
    import probe_providers as api

PACIFIC = ZoneInfo("America/Los_Angeles")
METRICS = {
    "firestore_reads": ("document/read_ops_count", 50_000, "type", {"LOOKUP", "QUERY"}),
    "firestore_writes": ("document/write_ops_count", 20_000, "op", {"CREATE", "UPDATE"}),
    "firestore_deletes": ("document/delete_ops_count", 20_000, None, {None}),
    "firestore_storage_bytes": ("storage/data_and_index_storage_bytes", 1024**3, None, {None}),
}


def fail(code):
    raise api.ProbeError(code)


def timestamp(value):
    if not isinstance(value, str):
        fail("invalid_timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("invalid_timestamp")
    if result.tzinfo is None:
        fail("invalid_timestamp")
    return result.astimezone(UTC)


def integer(value, *, wire_string=False):
    if wire_string and isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        fail("invalid_integer")
    return value


def pages(client, service, path, field, query=None):
    """Consume native page tokens completely, preserving no-data and partial errors."""
    query = dict(query or {})
    result = []
    seen = set()
    for _ in range(api.MAX_REQUESTS):
        response = client.get(service, path, query)
        if response.get("executionErrors") or response.get("unreachable"):
            fail("partial_provider_error")
        rows = response.get(field, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            fail("invalid_shape")
        result.extend(rows)
        token = response.get("nextPageToken", "")
        if not isinstance(token, str):
            fail("invalid_page_token")
        if not token:
            if not result:
                fail("no_data")
            return result
        if token in seen:
            fail("repeated_page_token")
        seen.add(token)
        query["pageToken"] = token
    fail("request_budget_exhausted")


def firebase_identity(client, project, now):
    identity = client.get("firebase", f"/v1beta1/projects/{project}")
    number = identity.get("projectNumber")
    if (
        identity.get("projectId") != project
        or identity.get("state") != "ACTIVE"
        or not isinstance(number, str)
        or not re.fullmatch(r"[1-9][0-9]+", number)
        or identity.get("name") not in {f"projects/{project}", f"projects/{number}"}
    ):
        fail("invalid_firebase_identity")
    api.check_billing(client, project)
    databases = pages(client, "firestore", f"/v1/projects/{project}/databases", "databases")
    # Do not assume (default) is the eligible database or silently ignore another DB.
    if len(databases) != 1:
        fail("unsupported_database_scope")
    database = databases[0]
    name = database.get("name", "")
    match = re.fullmatch(r"projects/([^/]+)/databases/([A-Za-z0-9_().-]+)", name)
    if (
        not match
        or match[1] not in {project, number}
        or database.get("freeTier") is not True
        or database.get("type") != "FIRESTORE_NATIVE"
        or database.get("deleteTime")
        or not re.fullmatch(r"[a-z0-9-]+", database.get("locationId", ""))
    ):
        fail("invalid_free_database")
    return {
        "project": project,
        "number": number,
        "database": match[2],
        "location": database["locationId"],
        "plan": "Spark",
        "observed_at": now.isoformat(),
    }


def firestore_metric(client, identity, name, now):
    metric, limit, label, classes = METRICS[name]
    storage = name == "firestore_storage_bytes"
    project = identity["project"]
    start = now.astimezone(PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    # Storage is instantaneous, and can legitimately predate today's reset.
    query_start = now - timedelta(hours=36) if storage else start
    aliases = {project, identity["number"], f"projects/{project}", f"projects/{identity['number']}"}
    scope_filter = " OR ".join(
        f'resource.labels.resource_container = "{alias}"' for alias in sorted(aliases)
    )
    rows = pages(
        client,
        "monitoring",
        f"/v3/projects/{project}/timeSeries",
        "timeSeries",
        {
            "filter": f'metric.type = "firestore.googleapis.com/{metric}" '
            'AND resource.type = "firestore.googleapis.com/Database" '
            f"AND ({scope_filter}) "
            f'AND resource.labels.database_id = "{identity["database"]}" '
            f'AND resource.labels.location = "{identity["location"]}"',
            "interval.startTime": query_start.isoformat(),
            "interval.endTime": now.isoformat(),
            "view": "FULL",
            "pageSize": 1000,
        },
    )
    groups = {}
    for row in rows:
        resource = row["resource"]
        labels = resource["labels"]
        if (
            resource["type"] != "firestore.googleapis.com/Database"
            or set(labels) != {"resource_container", "database_id", "location"}
            or labels["resource_container"] not in aliases
            or labels["database_id"] != identity["database"]
            or labels["location"] != identity["location"]
            or row["metric"]["type"] != f"firestore.googleapis.com/{metric}"
            or row["metricKind"] != ("GAUGE" if storage else "DELTA")
            or row["valueType"] != "INT64"
            or row["unit"] != ("By" if storage else "1")
        ):
            fail("invalid_metric_scope_or_type")
        labels = row["metric"].get("labels", {})
        if set(labels) != ({label} if label else set()):
            fail("unexpected_metric_labels")
        category = labels.get(label) if label else None
        if category not in classes:
            fail("unexpected_metric_labels")
        points = api.require_list(row["points"])
        groups.setdefault(category, []).extend(points)
    # Missing operation classes cannot be assumed to have zero usage.
    if set(groups) != classes:
        fail("incomplete_operation_classes")
    totals = []
    observations = []
    for points in groups.values():
        parsed = []
        for point in points:
            interval = point["interval"]
            end = timestamp(interval["endTime"])
            begin = end if storage else timestamp(interval["startTime"])
            if not query_start <= begin <= end <= now or (not storage and begin == end):
                fail("invalid_metric_interval")
            if set(point["value"]) != {"int64Value"}:
                fail("invalid_metric_value")
            parsed.append((begin, end, integer(point["value"]["int64Value"], wire_string=True)))
        parsed.sort()
        if len({(p[0], p[1]) for p in parsed}) != len(parsed):
            fail("duplicate_metric_point")
        if storage:
            # Never sum gauge samples across time or replace their native timestamp.
            totals.append(parsed[-1][2])
        else:
            cursor = start
            for begin, end, _ in parsed:
                if begin != cursor:
                    fail("incomplete_daily_coverage")
                cursor = end
            totals.append(sum(p[2] for p in parsed))
        observations.append(parsed[-1][1])
    if len(set(observations)) != 1:
        fail("inconsistent_observation_period")
    observed = observations[0]
    if not now - timedelta(hours=36) <= observed <= now:
        fail("stale_metric")
    return {
        "observation": "counter",
        "unit": "bytes" if storage else "count",
        "used": sum(totals),
        "limit": limit,
        "observed_at": observed.isoformat(),
        "scope": project,
        "source": f"Cloud Monitoring firestore.googleapis.com/{metric}",
        "window": "instant"
        if storage
        else {"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
    }


def github_cache(client, repository, now):
    usage = client.get("github", f"/repos/{repository}/actions/cache/usage")
    configuration = client.get("github", f"/repos/{repository}/actions/cache/storage-limit")
    used = integer(usage["active_caches_size_in_bytes"])
    configured = integer(configuration["max_cache_size_gb"]) * 1024**3
    if not 0 < configured <= 10 * 1024**3:
        fail("cache_configuration_exceeds_free")
    # Deliberately incomplete: usage/configuration do NOT establish monthly billing.
    return {
        "observation": "counter",
        "unit": "bytes",
        "used": used,
        "limit": 10 * 1024**3,
        "configured_limit": configured,
        "configuration_source": "GitHub repository cache storage-limit API",
        "observed_at": now.isoformat(),
        "scope": repository,
        "window": "instant",
        "source": "GitHub repository cache usage API",
    }


def collect(env, now, client=None):
    client = client or api.Client(env)
    fragments = {}
    checks = []

    def run(name, action):
        try:
            fragments[name] = action()
            status = "fragment_collected_not_complete_report"
        except api.ProbeError as error:
            status = str(error)
        except (KeyError, TypeError, ValueError, AttributeError, IndexError):
            status = "invalid_shape"
        checks.append({"check": name, "status": status})

    def identity():
        project = api.identifier(env, "VITE_FIREBASE_PROJECT_ID", r"[a-z][a-z0-9-]{4,28}[a-z0-9]")
        return firebase_identity(client, project, now)

    run("firebase_identity_and_spark", identity)
    for name in METRICS:
        if "firebase_identity_and_spark" not in fragments:
            checks.append({"check": name, "status": "missing_verified_database"})
        else:
            run(
                name,
                lambda name=name: firestore_metric(
                    client, fragments["firebase_identity_and_spark"], name, now
                ),
            )

    def cache():
        repository = api.identifier(env, "GITHUB_REPOSITORY", r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
        owner = api.identifier(env, "GITHUB_REPOSITORY_OWNER", r"[A-Za-z0-9-]+")
        if repository.split("/")[0] != owner:
            fail("invalid_scope")
        return github_cache(client, repository, now)

    run("github_cache_usage_and_configuration", cache)
    return {
        "status": "BLOCKED",
        "kind": "native_collection_incomplete",
        "attempted_at": now.isoformat(),
        "checks": checks,
        "unresolved_evidence": [
            "cloudflare_free_plans_and_complete_account_usage",
            "firestore_outbound_counter_or_current_three_surface_audit",
            "github_storage_sku_and_shared_included_allowance",
            "github_cache_monthly_billing",
            "github_automatic_billing_disabled",
            "official_limits_verified_renewal",
        ],
    }


def main():
    print(json.dumps(collect(os.environ, datetime.now(UTC)), indent=2))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
