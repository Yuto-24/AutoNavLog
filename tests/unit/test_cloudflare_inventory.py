"""Synthetic Cloudflare fixtures; inventory must never become plan/quota evidence."""

import copy
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts/static_ops"))
try:
    import cloudflare_inventory as cf
    import collect_providers as collection
    import collection_gate as gate
finally:
    sys.path.pop(0)

ACCOUNT = "a" * 32
NOW = datetime(2026, 10, 7, tzinfo=UTC)


class Client:
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.calls = []

    def get(self, service, path, query=None):
        self.calls.append((service, path, query))
        return self.responses.pop(0)


def page(rows, number=1, total=None, total_pages=1):
    return {
        "success": True, "errors": [], "result": copy.deepcopy(rows),
        "result_info": {
            "page": number, "per_page": cf.PAGE_SIZE, "count": len(rows),
            "total_count": len(rows) if total is None else total, "total_pages": total_pages,
        },
    }


def deployment(name="project-1", id="deployment-1"):
    return {
        "id": id, "project_id": name, "project_name": name,
        "created_on": "2026-10-01T00:00:00Z", "modified_on": "2026-10-01T00:01:00Z",
        "environment": "preview", "is_skipped": True,
        "build_config": {"web_analytics_token": "SECRET"},
        "env_vars": {"password": {"value": "SECRET"}},
    }


def subscription():
    return {
        "id": "subscription-1", "currency": "USD", "price": 0,
        "current_period_start": "2026-10-01T00:00:00Z",
        "current_period_end": "2026-11-01T00:00:00Z", "frequency": "monthly", "state": "Paid",
        "rate_plan": {
            "id": "free", "currency": "USD", "public_name": "Free Plan", "scope": "zone",
            "externally_managed": False, "is_contract": False, "sets": ["unmapped-product"],
        },
        "unrecognized_private_field": "SECRET",
    }


def test_all_project_and_deployment_pages_are_consumed_and_sensitive_fields_dropped():
    projects = [{"id": f"project-{i}", "name": f"project-{i}"} for i in range(21)]
    projects[0]["deployment_configs"] = {"production": {"env_vars": {"SECRET": "SECRET"}}}
    responses = [page(projects[:20], total=21, total_pages=2), page(projects[20:], 2, 21, 2)]
    records = [deployment("project-0", f"deployment-{i}") for i in range(21)]
    responses += [page(records[:20], total=21, total_pages=2), page(records[20:], 2, 21, 2)]
    responses += [page([deployment(f"project-{i}")]) for i in range(1, 21)]
    client = Client(responses)
    result = cf.pages_inventory(client, ACCOUNT)
    assert len(result["projects"]) == 21
    assert len(result["projects"][0]["deployments"]) == 21
    assert not client.responses
    assert all(service == "cloudflare" and query.keys() == {"page", "per_page"}
               for service, _, query in client.calls)
    assert client.calls[1][2]["page"] == client.calls[3][2]["page"] == 2
    assert result["coverage"] == "retained_inventory_not_quota"
    output = json.dumps(result)
    assert "SECRET" not in output and "pages_builds" not in output and '"used"' not in output
    assert result["projects"][0]["deployments"][0]["is_skipped"] is True
    assert result["projects"][0]["deployments"][0]["environment"] == "preview"


@pytest.mark.parametrize("field", ["page", "count", "per_page", "total_count", "total_pages"])
@pytest.mark.parametrize("value", [None, True, -1, 1.0, "1"])
def test_missing_or_non_integer_pagination_metadata_is_rejected(field, value):
    response = page([{"id": "p", "name": "p"}])
    response["result_info"][field] = value
    with pytest.raises(cf.api.ProbeError):
        cf.pages_inventory(Client([response]), ACCOUNT)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.pop("result_info"),
        lambda r: r.update(result=None),
        lambda r: r.update(result=[None]),
        lambda r: r.update(success=False),
        lambda r: r.update(success=1),
        lambda r: r.update(errors=[{"message": "SECRET"}]),
        lambda r: r["result_info"].update(count=2),
        lambda r: r["result_info"].update(page=2),
        lambda r: r["result_info"].update(per_page=1),
        lambda r: r["result_info"].update(total_count=2),
        lambda r: r["result_info"].update(total_pages=41, total_count=41),
        lambda r: r["result"][0].update(name="../../subscriptions"),
        lambda r: r["result"][0].update(name="project?secret=SECRET"),
    ],
)
def test_invalid_pages_never_return_partial_inventory_or_private_errors(mutation):
    response = page([{"id": "p", "name": "p"}])
    mutation(response)
    with pytest.raises(cf.api.ProbeError) as error:
        cf.pages_inventory(Client([response]), ACCOUNT)
    assert "SECRET" not in str(error.value)


@pytest.mark.parametrize("total,total_pages,second_id", [(3, 2, "b"), (2, 3, "b"), (2, 2, "a")])
def test_page_count_drift_and_duplicate_ids_are_rejected(total, total_pages, second_id):
    client = Client([
        page([{"id": "a", "name": "a"}], total=2, total_pages=2),
        page([{"id": second_id, "name": "b"}], 2, total, total_pages),
    ])
    with pytest.raises(cf.api.ProbeError):
        cf.pages_inventory(client, ACCOUNT)
    assert len(client.calls) == 2


def test_short_final_page_cannot_hide_missing_records():
    client = Client([
        page([{"id": "a", "name": "a"}], total=3, total_pages=2),
        page([{"id": "b", "name": "b"}], 2, 3, 2),
    ])
    with pytest.raises(cf.api.ProbeError, match="incomplete_inventory"):
        cf.pages_inventory(client, ACCOUNT)


def test_duplicate_project_name_cannot_select_a_different_project():
    client = Client([page([{"id": "a", "name": "same"}, {"id": "b", "name": "same"}])])
    with pytest.raises(cf.api.ProbeError, match="duplicate_project_name"):
        cf.pages_inventory(client, ACCOUNT)
    assert len(client.calls) == 1


def test_duplicate_subscription_cannot_satisfy_total_count():
    with pytest.raises(cf.api.ProbeError, match="duplicate_inventory_id"):
        cf.subscriptions_inventory(Client([page([subscription(), subscription()])]), ACCOUNT)


@pytest.mark.parametrize("collector", [cf.pages_inventory, cf.subscriptions_inventory])
def test_explicit_empty_inventory_is_not_zero_or_free(collector):
    with pytest.raises(cf.api.ProbeError, match="no_data"):
        collector(Client([page([])]), ACCOUNT)


@pytest.mark.parametrize("field,value", [
    ("project_id", "another"), ("project_name", "another"), ("is_skipped", 0),
    ("created_on", "invalid"), ("created_on", "2026-10-01T00:00:00"),
    ("modified_on", "2026-09-30T00:00:00Z"), ("environment", "unknown"),
])
def test_deployment_scope_and_native_timestamps_are_validated(field, value):
    record = deployment()
    record[field] = value
    with pytest.raises(cf.api.ProbeError):
        cf.pages_inventory(Client([page([{"id": "project-1", "name": "project-1"}]),
                                   page([record])]), ACCOUNT)


def test_complete_subscription_list_is_neither_product_plan_nor_billing_control():
    response = page([subscription()])
    del response["result_info"]["total_pages"]  # Not documented for subscriptions.
    client = Client([response])
    result = cf.subscriptions_inventory(client, ACCOUNT)
    assert result["coverage"] == "subscription_inventory_not_plan"
    assert client.calls == [("cloudflare", f"/client/v4/accounts/{ACCOUNT}/subscriptions", None)]
    assert "SECRET" not in json.dumps(result)
    assert "plans" not in result and "automatic_billing" not in result
    assert "observed_at" not in result and "limits_checked_at" not in result


def test_subscription_pagination_cannot_guess_undocumented_query_parameters():
    client = Client([page([subscription()], total=2)])
    with pytest.raises(cf.api.ProbeError, match="unsupported_subscription_pagination"):
        cf.subscriptions_inventory(client, ACCOUNT)
    assert len(client.calls) == 1


@pytest.mark.parametrize("field,value", [
    ("price", None), ("price", True), ("price", -1), ("price", float("nan")),
    ("price", float("inf")), ("rate_plan", {}), ("state", "unknown"),
    ("current_period_end", "2026-09-01T00:00:00Z"),
])
def test_invalid_subscription_cannot_become_a_plan(field, value):
    record = subscription()
    record[field] = value
    with pytest.raises(cf.api.ProbeError):
        cf.subscriptions_inventory(Client([page([record])]), ACCOUNT)


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_timestamp_overflow_is_redacted_and_does_not_abort_other_inventory(value):
    record = subscription()
    record["current_period_start"] = value
    client = Client([page([record]), page([{"id": "project-1", "name": "project-1"}]),
                     page([deployment()])])
    fragments, checks = collection.collect_fragments(
        {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT}, NOW, client,
    )
    assert checks[-2]["status"] == "invalid_inventory_timestamp"
    assert checks[-1]["status"] == "inventory_validated_not_evidence"
    assert "cloudflare_subscriptions_inventory" not in fragments
    assert "cloudflare_pages_inventory" in fragments
    assert value not in json.dumps(checks)


def test_real_client_budget_bounds_inventory_requests():
    calls = []

    def open_request(request, **kwargs):
        calls.append(request)
        raw = page([{"id": "p", "name": "p"}], total=2, total_pages=2)
        response = io.BytesIO(json.dumps(raw).encode())
        response.status = 200
        return response

    client = cf.api.Client({"STATIC_CLOUDFLARE_READ_TOKEN": "SECRET"},
                           opener=SimpleNamespace(open=open_request))
    client.requests = cf.api.MAX_REQUESTS - 1
    with pytest.raises(cf.api.ProbeError, match="request_budget_exhausted"):
        cf.pages_inventory(client, ACCOUNT)
    assert len(calls) == 1


@pytest.mark.parametrize("publication", [False, True])
def test_inventory_collection_cannot_clear_gate_or_export_contracts(monkeypatch, publication):
    env = {"CLOUDFLARE_ACCOUNT_ID": ACCOUNT}

    def responses():
        return [page([subscription()]), page([{"id": "project-1", "name": "project-1"}]),
                page([deployment()])]

    fragments, checks = collection.collect_fragments(env, NOW, Client(responses()))
    assert len(fragments) == 2
    assert all(x["status"] == "inventory_validated_not_evidence" for x in checks[-2:])
    summary = collection.collect(env, NOW, Client(responses()))
    assert summary["status"] == "BLOCKED"
    monkeypatch.setattr(
        gate.verify_limits, "audit", lambda *args: {"status": "BLOCKED", "checks": []},
    )
    result = gate.evaluate(env, NOW, client=Client(responses()), publication=publication,
                           clock=lambda: NOW)
    assert result["status"] == "BLOCKED"
    for output in (summary, result):
        encoded = json.dumps(output)
        assert all(value not in encoded for value in (ACCOUNT, "SECRET", "subscription-1", "Paid"))
        assert all(key not in output for key in ("plans", "metrics", "limits_checked_at"))
