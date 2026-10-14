"""Private, bounded Cloudflare inventories, never plan or quota evidence.

Pagination consistency covers only the returned inventory. These APIs do not prove
deleted-build retention, quota accounting, or account-wide billing controls.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

try:
    from . import probe_providers as api
except ImportError:
    import probe_providers as api

PAGE_SIZE = 20


def fail(code):
    raise api.ProbeError(code)


def text(value, pattern=r"[^\x00-\x1f\x7f]{1,256}"):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        fail("invalid_inventory_field")
    return value


def integer(value):
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        fail("invalid_page_metadata")
    return value


def timestamp(value):
    """Keep native timestamps; never replace a missing observation with now."""
    if not isinstance(value, str):
        fail("invalid_inventory_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            fail("invalid_inventory_timestamp")
        return parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        fail("invalid_inventory_timestamp")


def envelope(response, page):
    """Require explicit counts instead of treating omitted metadata as complete."""
    if not isinstance(response, dict) or response.get("success") is not True:
        fail("invalid_inventory_response")
    if response.get("errors") or response.get("error"):
        fail("provider_error")
    rows = response.get("result")
    info = response.get("result_info")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        fail("invalid_inventory_response")
    if not isinstance(info, dict):
        fail("missing_page_metadata")
    counts = {key: integer(info.get(key)) for key in ("page", "per_page", "count", "total_count")}
    if (
        counts["page"] != page or counts["per_page"] < 1
        or counts["count"] != len(rows) or len(rows) > counts["per_page"]
        or len(rows) > counts["total_count"]
    ):
        fail("inconsistent_page_metadata")
    if not rows:
        fail("no_data")
    return rows, info, counts


def paginated(client, path, normalize):
    """Read every declared Pages page; reject gaps, repeats and observed drift."""
    result = []
    ids = set()
    expected = None
    for page in range(1, api.MAX_REQUESTS + 1):
        response = client.get("cloudflare", path, {"page": page, "per_page": PAGE_SIZE})
        rows, info, counts = envelope(response, page)
        total_pages = integer(info.get("total_pages"))
        if (
            not page <= total_pages <= api.MAX_REQUESTS
            or counts["per_page"] != PAGE_SIZE
            or not total_pages <= counts["total_count"] <= total_pages * PAGE_SIZE
        ):
            fail("inconsistent_page_metadata")
        snapshot = (counts["total_count"], total_pages, counts["per_page"])
        if expected is not None and snapshot != expected:
            fail("inventory_changed_during_pagination")
        expected = snapshot
        for row in rows:
            item = normalize(row)
            if item["id"] in ids:
                fail("duplicate_inventory_id")
            ids.add(item["id"])
            result.append(item)
        if len(result) > counts["total_count"]:
            fail("inconsistent_page_metadata")
        if page == total_pages:
            if len(result) != counts["total_count"]:
                fail("incomplete_inventory")
            return result
    fail("request_budget_exhausted")


def project(row):
    """Discard build config, environment variables, source metadata and tokens."""
    return {"id": text(row.get("id")), "name": text(row.get("name"), r"[a-z0-9][a-z0-9-]{0,255}")}


def deployment(row, identity):
    if row.get("project_id") != identity["id"] or row.get("project_name") != identity["name"]:
        fail("invalid_deployment_scope")
    created = timestamp(row.get("created_on"))
    modified = timestamp(row.get("modified_on"))
    if modified < created:
        fail("invalid_inventory_timestamp")
    if (
        row.get("environment") not in ("production", "preview")
        or type(row.get("is_skipped")) is not bool
    ):
        fail("invalid_deployment_shape")
    return {
        "id": text(row.get("id")), "project_id": identity["id"],
        "created_on": created.isoformat(), "modified_on": modified.isoformat(),
        "environment": row["environment"], "is_skipped": row["is_skipped"],
    }


def pages_inventory(client, account):
    """Return retained deployments, including preview/skipped, without counting builds."""
    account = text(account, r"[a-f0-9]{32}")
    path = f"/client/v4/accounts/{account}/pages/projects"
    projects = paginated(client, path, project)
    if len({item["name"] for item in projects}) != len(projects):
        fail("duplicate_project_name")
    for item in projects:
        item["deployments"] = paginated(
            client, f"{path}/{item['name']}/deployments",
            lambda row, identity=item: deployment(row, identity),
        )
    return {"account": account, "coverage": "retained_inventory_not_quota", "projects": projects}


def subscription(row):
    """Validate an explicit contract record without inferring product or Free status."""
    rate = row.get("rate_plan")
    if not isinstance(rate, dict):
        fail("invalid_subscription_shape")
    price = row.get("price")
    if (
        type(price) not in (int, float) or price < 0
        or (type(price) is float and not math.isfinite(price))
    ):
        fail("invalid_subscription_price")
    start = timestamp(row.get("current_period_start"))
    end = timestamp(row.get("current_period_end"))
    if end <= start:
        fail("invalid_inventory_timestamp")
    if row.get("frequency") not in ("weekly", "monthly", "quarterly", "yearly"):
        fail("invalid_subscription_shape")
    if row.get("state") not in (
        "Trial", "Provisioned", "Paid", "AwaitingPayment", "Cancelled", "Failed", "Expired",
    ):
        fail("invalid_subscription_shape")
    if rate.get("id") not in (
        "free", "lite", "pro", "pro_plus", "business", "enterprise", "partners_free",
        "partners_pro", "partners_business", "partners_enterprise",
    ):
        fail("unsupported_rate_plan")
    if any(type(rate.get(key)) is not bool for key in ("externally_managed", "is_contract")):
        fail("invalid_subscription_shape")
    sets = rate.get("sets")
    if not isinstance(sets, list):
        fail("invalid_subscription_shape")
    return {
        "id": text(row.get("id"), r"[^\x00-\x1f\x7f]{1,32}"),
        "currency": text(row.get("currency")), "price": price,
        "current_period_start": start.isoformat(), "current_period_end": end.isoformat(),
        "frequency": row["frequency"], "state": row["state"],
        "rate_plan": {
            "id": rate["id"], "currency": text(rate.get("currency")),
            "scope": text(rate.get("scope")), "public_name": text(rate.get("public_name")),
            "externally_managed": rate["externally_managed"], "is_contract": rate["is_contract"],
            "sets": [text(value) for value in sets],
        },
    }


def subscriptions_inventory(client, account):
    """Accept only explicit single-page coverage; pagination inputs are undocumented."""
    account = text(account, r"[a-f0-9]{32}")
    response = client.get("cloudflare", f"/client/v4/accounts/{account}/subscriptions")
    rows, info, counts = envelope(response, 1)
    if counts["total_count"] != len(rows):
        fail("unsupported_subscription_pagination")
    if "total_pages" in info and integer(info["total_pages"]) != 1:
        fail("inconsistent_page_metadata")
    records = [subscription(row) for row in rows]
    if len({row["id"] for row in records}) != len(records):
        fail("duplicate_inventory_id")
    return {
        "account": account, "coverage": "subscription_inventory_not_plan", "subscriptions": records,
    }
