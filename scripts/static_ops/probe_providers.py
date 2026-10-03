"""Read-only API capability probes, NOT a quota report or publication authorization.

Only fixed check names and status codes leave this process. Raw provider responses,
including owner-wide billing, stay in memory. Successful transport does not prove
quota completeness, included allowance, zero billing, or a Free/Spark plan.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

MAX_BYTES = 8 * 1024 * 1024
MAX_REQUESTS = 40
HOSTS = {
    "cloudflare": "api.cloudflare.com",
    "github": "api.github.com",
    "billing": "cloudbilling.googleapis.com",
    "firestore": "firestore.googleapis.com",
    "monitoring": "monitoring.googleapis.com",
    "firebase": "firebase.googleapis.com",
}
TOKEN_ENV = {
    "cloudflare": "STATIC_CLOUDFLARE_READ_TOKEN",
    "github": "STATIC_GITHUB_READ_TOKEN",
    "billing": "STATIC_GOOGLE_ACCESS_TOKEN",
    "firestore": "STATIC_GOOGLE_ACCESS_TOKEN",
    "monitoring": "STATIC_GOOGLE_ACCESS_TOKEN",
    "firebase": "STATIC_GOOGLE_ACCESS_TOKEN",
}
# These are unresolved evidence contracts, not exceptions that permit publication.
BLOCKERS = [
    "cloudflare_free_plan_and_account_build_coverage",
    "workers_all_requests_including_cached_and_rejected_coverage",
    "firestore_free_database_storage_and_complete_daily_usage",
    "firestore_outbound_counter_or_current_three_surface_audit",
    "github_storage_sku_and_shared_included_allowance",
    "github_automatic_billing_disabled",
    "official_limits_verified_renewal",
]


class ProbeError(Exception):
    """Only locally selected, non-secret codes may be emitted."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("redirect_rejected")


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProbeError("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise ProbeError("nonfinite_json")

    def decimal(value):
        result = float(value)
        if not math.isfinite(result):
            raise ProbeError("nonfinite_json")
        return result

    try:
        value = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=decimal
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ProbeError("invalid_json") from None
    if not isinstance(value, dict):
        raise ProbeError("invalid_shape")
    return value


class Client:
    def __init__(self, env, *, opener=None, clock=time.monotonic):
        self.env = env
        self.opener = opener or build_opener(NoRedirect)
        self.clock = clock
        self.deadline = clock() + 180
        self.requests = 0

    def get(self, service, path, query=None, *, graphql=None):
        token = self.env.get(TOKEN_ENV[service], "")
        if not token:
            raise ProbeError("missing_credential")
        if (
            not path.startswith("/")
            or path.startswith("//")
            or urlsplit(path).query
            or urlsplit(path).fragment
            or any(c in path for c in ("\\", "\n", "\r"))
        ):
            raise ProbeError("invalid_path")
        self.requests += 1
        if self.requests > MAX_REQUESTS or self.clock() >= self.deadline:
            raise ProbeError("request_budget_exhausted")
        url = f"https://{HOSTS[service]}{path}"
        if query:
            url += "?" + urlencode(query)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "User-Agent": "AutoNavLog-ProviderProbe/1.0",
        }
        if service == "github":
            headers.update(Accept="application/vnd.github+json")
            headers["X-GitHub-Api-Version"] = "2026-03-10"
        body = None
        if graphql is not None:
            if service != "cloudflare" or path != "/client/v4/graphql":
                raise ProbeError("invalid_graphql_target")
            # Query is compiled below; no arbitrary mutation entrypoint in the CLI.
            if not graphql["query"].lstrip().startswith("query "):
                raise ProbeError("mutation_rejected")
            body = json.dumps(graphql).encode()
            headers["Content-Type"] = "application/json"
        try:
            with self.opener.open(
                Request(url, data=body, headers=headers),
                timeout=min(15, self.deadline - self.clock()),
            ) as response:
                raw = bytearray()
                while True:
                    if self.clock() >= self.deadline:
                        raise ProbeError("request_budget_exhausted")
                    chunk = response.read1(min(64 * 1024, MAX_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ProbeError("response_too_large")
            result = strict_json(raw)
        except HTTPError as error:
            # Never print the URL, reason, body or headers; these can contain secrets.
            raise ProbeError(f"http_{error.code}") from None
        except (HTTPException, URLError, OSError, ValueError):
            raise ProbeError("transport_failed") from None
        if result.get("error") or result.get("errors") or result.get("success") is False:
            raise ProbeError("provider_error")
        return result


def identifier(env, key, pattern):
    value = env.get(key, "")
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ProbeError("missing_or_invalid_scope")
    return value


def require_list(value):
    if not isinstance(value, list):
        raise ProbeError("invalid_shape")
    if not value:
        raise ProbeError("no_data")
    return value


def check_cf_list(client, account, suffix):
    response = client.get("cloudflare", f"/client/v4/accounts/{account}/{suffix}")
    if response.get("success") is not True:
        raise ProbeError("invalid_shape")
    require_list(response.get("result"))
    # A list is diagnostic only: this probe does not claim complete pagination.
    return "reachable_not_evidence"


def check_workers(client, account, now):
    query = """query ProbeWorkers($account: string, $start: string, $end: string) {
      viewer { accounts(filter: {accountTag: $account}) {
        workersInvocationsAdaptive(limit: 1,
          filter: {datetime_geq: $start, datetime_leq: $end}) { sum { requests } }
      } }
    }"""
    response = client.get(
        "cloudflare",
        "/client/v4/graphql",
        graphql={
            "query": query,
            "variables": {
                "account": account,
                "start": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
                "end": now.isoformat(),
            },
        },
    )
    accounts = require_list(response["data"]["viewer"]["accounts"])
    if len(accounts) != 1:
        raise ProbeError("ambiguous_scope")
    require_list(accounts[0]["workersInvocationsAdaptive"])
    # Invocation analytics does not by itself establish cached/rejected coverage.
    return "reachable_not_evidence"


def check_billing(client, project):
    response = client.get("billing", f"/v1/projects/{project}/billingInfo")
    if response.get("projectId") != project or type(response.get("billingEnabled")) is not bool:
        raise ProbeError("invalid_shape_or_scope")
    if response["billingEnabled"]:
        raise ProbeError("billing_enabled")
    # A disabled Cloud Billing account alone is not asserted to be Firebase Spark.
    return "billing_disabled_not_plan_evidence"


def check_series(client, project, metric, now):
    start = now.astimezone(ZoneInfo("America/Los_Angeles")).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    response = client.get(
        "monitoring",
        f"/v3/projects/{project}/timeSeries",
        {
            "filter": f'metric.type = "firestore.googleapis.com/{metric}" '
            'AND resource.type = "firestore.googleapis.com/Database" '
            f'AND resource.labels.resource_container = "{project}"',
            "interval.startTime": start.isoformat(),
            "interval.endTime": now.isoformat(),
            "view": "FULL",
            "pageSize": 100,
        },
    )
    require_list(response.get("timeSeries", []))
    if response.get("nextPageToken"):
        raise ProbeError("incomplete_page")
    if response.get("executionErrors"):
        raise ProbeError("partial_provider_error")
    return "reachable_not_evidence"


def check_github(client, repository, suffix, field):
    response = client.get("github", f"/repos/{repository}{suffix}")
    value = response.get(field)
    if type(value) is not int or value < 0:
        raise ProbeError("invalid_shape")
    return "reachable_not_evidence"


def check_github_billing(client, owner, now):
    # The selected production owner is a personal account. Do not broaden to an org.
    response = client.get(
        "github",
        f"/users/{owner}/settings/billing/usage",
        {
            "year": now.year,
            "month": now.month,
        },
    )
    require_list(response.get("usageItems"))
    if response.get("nextPageToken"):
        raise ProbeError("incomplete_page")
    # Deliberately emit no product/SKU/quantity/price/repository/billing rows.
    # netAmount=0 cannot prove the shared included allowance or disabled billing.
    return "reachable_not_evidence"


def probe(env, now, client=None):
    client = client or Client(env)
    checks = []

    def run(name, action):
        try:
            status = action()
        except ProbeError as error:
            status = str(error)
        except (KeyError, TypeError, IndexError, AttributeError):
            status = "invalid_shape"
        checks.append({"check": name, "status": status})

    def cf():
        return identifier(env, "CLOUDFLARE_ACCOUNT_ID", r"[a-f0-9]{32}")

    def project():
        return identifier(env, "VITE_FIREBASE_PROJECT_ID", r"[a-z][a-z0-9-]{4,28}[a-z0-9]")

    def owner():
        return identifier(env, "GITHUB_REPOSITORY_OWNER", r"[A-Za-z0-9-]+")

    def repository():
        repo = identifier(env, "GITHUB_REPOSITORY", r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+")
        if repo.split("/")[0] != owner():
            raise ProbeError("invalid_scope")
        return repo

    run("cloudflare_subscriptions", lambda: check_cf_list(client, cf(), "subscriptions"))
    run("cloudflare_pages_projects", lambda: check_cf_list(client, cf(), "pages/projects"))
    run("cloudflare_worker_invocations", lambda: check_workers(client, cf(), now))
    run("firebase_billing", lambda: check_billing(client, project()))
    for operation in ("read", "write", "delete"):
        run(
            f"firestore_{operation}s",
            lambda op=operation: check_series(client, project(), f"document/{op}_ops_count", now),
        )

    run(
        "firestore_storage",
        lambda: check_series(client, project(), "storage/data_and_index_storage_bytes", now),
    )

    # Descriptor discovery does not prove outbound counter availability or absence.
    def descriptors():
        response = client.get(
            "monitoring",
            f"/v3/projects/{project()}/metricDescriptors",
            {
                "filter": 'metric.type = starts_with("firestore.googleapis.com/")',
                "pageSize": 100,
            },
        )
        require_list(response.get("metricDescriptors", []))
        if response.get("nextPageToken"):
            raise ProbeError("incomplete_page")
        return "reachable_not_evidence"

    run("firestore_metric_descriptors", descriptors)
    run(
        "github_cache_usage",
        lambda: check_github(
            client, repository(), "/actions/cache/usage", "active_caches_size_in_bytes"
        ),
    )
    run(
        "github_cache_configuration",
        lambda: check_github(
            client, repository(), "/actions/cache/storage-limit", "max_cache_size_gb"
        ),
    )
    run("github_billing_usage", lambda: check_github_billing(client, owner(), now))
    return {
        "status": "BLOCKED",
        "kind": "capability_probe_not_quota_evidence",
        "attempted_at": now.isoformat(),
        "checks": checks,
        "unresolved_evidence": BLOCKERS,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(probe(os.environ, datetime.now(UTC)), indent=2))
    # Probe reachability never authorizes publishing or refreshes evidence timestamps.
    raise SystemExit(1)


if __name__ == "__main__":
    main()
