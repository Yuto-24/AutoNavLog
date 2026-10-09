"""Offline native-API fixtures; successful probes cannot authorize publication."""

import importlib.util
import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from http.client import BadStatusLine, IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "provider_probes", ROOT / "scripts/static_ops/probe_providers.py"
)
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)
NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
ENV = {
    "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
    "VITE_FIREBASE_PROJECT_ID": "firebase-project",
    "GITHUB_REPOSITORY_OWNER": "owner",
    "GITHUB_REPOSITORY": "owner/repo",
    "STATIC_CLOUDFLARE_READ_TOKEN": "SECRET-CF",
    "STATIC_GITHUB_READ_TOKEN": "SECRET-GH",
    "STATIC_GOOGLE_ACCESS_TOKEN": "SECRET-GCP",
}


def client(raw, requests=None):
    def open_request(request, **kwargs):
        if requests is not None:
            requests.append(request)
        response = io.BytesIO(raw)
        response.status = 200
        return response

    return p.Client(ENV, opener=SimpleNamespace(open=open_request))


@pytest.mark.parametrize(
    "raw", [b"null", b"[]", b"invalid", b'{"a":NaN}', b'{"a":1e999}', b'{"a":1,"a":2}']
)
def test_invalid_json_never_becomes_usage(raw):
    with pytest.raises(p.ProbeError):
        client(raw).get("github", "/repos/owner/repo/actions/cache/usage")


@pytest.mark.parametrize("status", [201, 204, 206, 304])
def test_non_200_response_cannot_become_complete_provider_evidence(status):
    response = io.BytesIO(b'{"active_caches_size_in_bytes":0}')
    response.status = status
    c = p.Client(ENV, opener=SimpleNamespace(open=lambda *a, **kw: response))
    with pytest.raises(p.ProbeError, match="unexpected_http_status"):
        c.get("github", "/repos/owner/repo/actions/cache/usage")


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_redirect_rejected_without_forwarding_credentials(code):
    with pytest.raises(p.ProbeError, match="redirect_rejected"):
        p.NoRedirect().redirect_request(None, None, code, "secret", {}, "https://evil.test")


@pytest.mark.parametrize(
    "error",
    [
        HTTPError("https://secret.test", 403, "SECRET", {}, io.BytesIO(b"SECRET")),
        URLError("SECRET"),
        TimeoutError("SECRET"),
        BadStatusLine("SECRET-provider-response"),
        IncompleteRead(b"SECRET-response-body"),
    ],
)
def test_network_errors_do_not_expose_response_or_credentials(error):
    def fail(*args, **kwargs):
        raise error

    c = p.Client(ENV, opener=SimpleNamespace(open=fail))
    with pytest.raises(p.ProbeError) as result:
        c.get("github", "/repos/owner/repo")
    assert "SECRET" not in str(result.value)
    assert "secret.test" not in str(result.value)


def test_request_is_bound_to_provider_and_budgeted():
    requests = []
    c = client(b"{}", requests)
    c.get("github", "/repos/owner/repo", {"month": 10})
    assert requests[0].full_url == "https://api.github.com/repos/owner/repo?month=10"
    assert requests[0].get_method() == "GET"
    assert requests[0].get_header("Authorization") == "Bearer SECRET-GH"
    assert requests[0].get_header("X-github-api-version") == "2026-03-10"
    c.requests = p.MAX_REQUESTS
    with pytest.raises(p.ProbeError, match="budget"):
        c.get("github", "/repos/owner/repo")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "path", ["//evil.test", "https://evil.test", "/a?secret=x", "/a#x", "/a\nb"]
)
def test_unsafe_paths_never_send_credentials(path):
    requests = []
    with pytest.raises(p.ProbeError, match="invalid_path"):
        client(b"{}", requests).get("github", path)
    assert requests == []


def test_oversize_and_deadline_fail_closed(monkeypatch):
    monkeypatch.setattr(p, "MAX_BYTES", 10)
    with pytest.raises(p.ProbeError, match="too_large"):
        client(b"x" * 11).get("github", "/repos/owner/repo")
    c = client(b"{}")
    c.clock = lambda: c.deadline + 1
    with pytest.raises(p.ProbeError, match="budget"):
        c.get("github", "/repos/owner/repo")


def test_missing_credentials_does_not_attempt_network():
    def fail(*args, **kwargs):
        pytest.fail("network called without credentials")

    c = p.Client({}, opener=SimpleNamespace(open=fail))
    result = p.probe({k: v for k, v in ENV.items() if "TOKEN" not in k}, NOW, c)
    assert {x["status"] for x in result["checks"]} == {"missing_credential"}
    assert result["status"] == "BLOCKED"


@pytest.mark.parametrize(
    "accounts,expected",
    [
        ([], "accounts_empty"),
        ([{"workersInvocationsAdaptive": []}], "invocations_empty"),
        (None, "invalid_shape"),
        ({}, "invalid_shape"),
        ([{"workersInvocationsAdaptive": None}], "invalid_shape"),
        ([{"workersInvocationsAdaptive": {}}], "invalid_shape"),
        ([{}, {}], "ambiguous_scope"),
    ],
)
def test_no_cloudflare_data_is_not_zero(accounts, expected):
    raw = {"data": {"viewer": {"accounts": accounts}}, "errors": None}
    with pytest.raises(p.ProbeError, match=expected):
        p.check_workers(client(json.dumps(raw).encode()), "a" * 32, NOW)


@pytest.mark.parametrize("rows", [
    [None], [{}], [{"sum": None}], [{"sum": {}}],
    [{"sum": {"requests": True}}], [{"sum": {"requests": -1}}],
    [{"sum": {"requests": "0"}}], [{"sum": {"requests": 0}}] * 2,
])
def test_workers_malformed_nonempty_data_is_not_reachability(rows):
    raw = {"data": {"viewer": {"accounts": [{"workersInvocationsAdaptive": rows}]}}}
    result = p.probe(
        ENV, NOW, client(json.dumps(raw).encode()), only="cloudflare_worker_invocations",
    )
    assert result["checks"] == [
        {"check": "cloudflare_worker_invocations", "status": "invalid_shape"},
    ]
    assert result["status"] == "BLOCKED"


@pytest.mark.parametrize("value", [0, 1, 1.5])
def test_workers_valid_analytics_is_not_quota_and_query_remains_one_utc_day_read(value):
    calls = []
    raw = {"data": {"viewer": {"accounts": [{"workersInvocationsAdaptive": [
        {"sum": {"requests": value}, "private": "SECRET"},
    ]}]}}}
    local_now = NOW.astimezone(timezone(timedelta(hours=9)))
    result = p.probe(ENV, local_now, client(json.dumps(raw).encode(), calls),
                     only="cloudflare_worker_invocations")
    assert len(calls) == 1
    query = json.loads(calls[0].data)
    assert query["variables"] == {
        "account": ENV["CLOUDFLARE_ACCOUNT_ID"],
        "start": NOW.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(),
        "end": NOW.isoformat(),
    }
    assert "workersInvocationsAdaptive(limit: 1" in query["query"]
    assert "scriptName" not in query["query"] and "dimensions" not in query["query"]
    assert result["checks"][0]["status"] == "reachable_not_evidence"
    assert result["status"] == "BLOCKED"
    assert "metrics" not in result and "plans" not in result and "limits_checked_at" not in result
    assert "SECRET" not in json.dumps(result)


def test_workers_naive_time_fails_before_request():
    calls = []
    with pytest.raises(p.ProbeError, match="invalid_observation_time"):
        p.check_workers(client(b"{}", calls), "a" * 32, NOW.replace(tzinfo=None))
    assert calls == []


def worker_settings_response(**changes):
    settings = {
        "enabled": True, "availableFields": ["sum_requests", "PRIVATE_FIELD"],
        "maxDuration": 86400, "notOlderThan": 86400,
        "maxPageSize": 100, "maxNumberOfFields": 30,
        "private": "SECRET",
    }
    settings.update(changes)
    return {"data": {"viewer": {"accounts": [{"settings": {
        "workersInvocationsAdaptive": settings,
    }}]}}}


@pytest.mark.parametrize("changes,expected", [
    ({}, "settings_compatible_not_quota_evidence"),
    ({"enabled": False}, "dataset_disabled"),
    ({"availableFields": []}, "required_field_unavailable"),
    ({"availableFields": ["requests"]}, "required_field_unavailable"),
    ({"maxDuration": 43199}, "window_not_supported"),
    ({"notOlderThan": 43199}, "window_not_supported"),
    ({"maxDuration": 43200, "notOlderThan": 43200}, "settings_compatible_not_quota_evidence"),
    ({"maxPageSize": 0}, "query_limits_not_supported"),
    ({"maxNumberOfFields": 0}, "query_limits_not_supported"),
    ({"enabled": 1}, "settings_invalid"),
    ({"availableFields": None}, "settings_invalid"),
    ({"availableFields": [None]}, "settings_invalid"),
])
def test_worker_settings_one_read_has_only_fixed_statuses(changes, expected):
    calls = []
    raw = worker_settings_response(**changes)
    result = p.probe(ENV, NOW, client(json.dumps(raw).encode(), calls),
                     only="cloudflare_worker_settings")
    assert len(calls) == 1
    payload = json.loads(calls[0].data)
    assert payload["variables"] == {"account": ENV["CLOUDFLARE_ACCOUNT_ID"]}
    assert "settings { workersInvocationsAdaptive" in payload["query"]
    assert "sum {" not in payload["query"] and "mutation" not in payload["query"]
    assert result["checks"] == [{"check": "cloudflare_worker_settings", "status": expected}]
    assert result["status"] == "BLOCKED"
    output = json.dumps(result)
    assert all(value not in output for value in ("SECRET", "PRIVATE_FIELD", "86400", "maxPageSize"))
    assert all(key not in result for key in ("metrics", "plans", "limits_checked_at"))


@pytest.mark.parametrize(
    "field", ["maxDuration", "notOlderThan", "maxPageSize", "maxNumberOfFields"]
)
@pytest.mark.parametrize("value", [None, True, -1, "86400", 86400.0])
def test_worker_settings_invalid_limits_are_not_defaults(field, value):
    raw = worker_settings_response(**{field: value})
    with pytest.raises(p.ProbeError, match="settings_invalid"):
        p.check_worker_settings(client(json.dumps(raw).encode()), "a" * 32, NOW)


@pytest.mark.parametrize("accounts,expected", [
    ([], "accounts_empty"), ([{}, {}], "ambiguous_scope"),
    (None, "settings_invalid"), ([{}], "settings_invalid"),
    ([{"settings": {"workersInvocationsAdaptive": None}}], "settings_invalid"),
])
def test_worker_settings_missing_account_or_metadata_never_implies_availability(accounts, expected):
    raw = {"data": {"viewer": {"accounts": accounts}}}
    with pytest.raises(p.ProbeError, match=expected):
        p.check_worker_settings(client(json.dumps(raw).encode()), "a" * 32, NOW)


def test_worker_settings_utc_window_keeps_fractional_seconds_and_rejects_naive_time():
    raw = worker_settings_response(maxDuration=43200)
    local_now = NOW.replace(microsecond=1).astimezone(timezone(timedelta(hours=14)))
    assert p.check_worker_settings(client(json.dumps(raw).encode()), "a" * 32, local_now) == (
        "window_not_supported"
    )
    calls = []
    with pytest.raises(p.ProbeError, match="invalid_observation_time"):
        p.check_worker_settings(client(b"{}", calls), "a" * 32, NOW.replace(tzinfo=None))
    assert calls == []


def test_graphql_error_and_mutation_rejected():
    with pytest.raises(p.ProbeError, match="provider_error"):
        p.check_workers(client(b'{"errors":[{"message":"SECRET"}]}'), "a" * 32, NOW)
    with pytest.raises(p.ProbeError, match="mutation"):
        client(b"{}").get("cloudflare", "/client/v4/graphql", graphql={"query": "mutation x {}"})


@pytest.mark.parametrize(
    "response,expected",
    [
        ({}, "no_data"),
        ({"timeSeries": []}, "no_data"),
        ({"timeSeries": [{}], "nextPageToken": "SECRET"}, "incomplete_page"),
        (
            {"timeSeries": [{}], "executionErrors": [{"message": "SECRET"}]},
            "partial_provider_error",
        ),
    ],
)
def test_firestore_missing_or_partial_is_not_spark_exception(response, expected):
    with pytest.raises(p.ProbeError, match=expected):
        p.check_series(
            client(json.dumps(response).encode()),
            "firebase-project",
            "document/read_ops_count",
            NOW,
        )


def test_firestore_query_uses_current_pacific_day_and_project_filter():
    requests = []
    c = client(b'{"timeSeries":[{}]}', requests)
    p.check_series(c, "firebase-project", "document/read_ops_count", NOW)
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(requests[0].full_url).query)
    assert query["interval.startTime"] == ["2026-10-03T00:00:00-07:00"]
    assert 'resource.labels.resource_container = "firebase-project"' in query["filter"][0]
    assert query["interval.endTime"] == [NOW.isoformat()]
    assert 'resource.type = "firestore.googleapis.com/Database"' in query["filter"][0]


@pytest.mark.parametrize("enabled", [None, 0, "false", True])
def test_billing_requires_explicit_boolean_false(enabled):
    c = client(json.dumps({"projectId": "firebase-project", "billingEnabled": enabled}).encode())
    with pytest.raises(p.ProbeError):
        p.check_billing(c, "firebase-project")


def test_github_gross_discount_net_never_infer_shared_allowance_or_publish_rows():
    # This proves API access only. 0.02 gross with 0 net is not a confirmed bill;
    # nor does net=0 prove included allowance rather than promotional credit.
    response = {
        "usageItems": [
            {
                "product": "Actions",
                "sku": "UNVERIFIED-SKU",
                "grossAmount": 0.02,
                "discountAmount": 0.02,
                "netAmount": 0,
                "repositoryName": "SECRET-private-repo",
            }
        ]
    }
    requests = []
    status = p.check_github_billing(client(json.dumps(response).encode(), requests), "owner", NOW)
    assert status == "reachable_not_evidence"
    assert "SECRET" not in status
    assert "year=2026&month=10" in requests[0].full_url


def test_probe_continues_independent_providers_and_never_returns_report():
    class Fake:
        def get(self, service, *args, **kwargs):
            if service == "cloudflare":
                raise p.ProbeError("http_403")
            if service == "billing":
                return {"projectId": "firebase-project", "billingEnabled": False}
            if service == "monitoring":
                return {"timeSeries": [{}], "metricDescriptors": [{}]}
            return {"active_caches_size_in_bytes": 0, "max_cache_size_gb": 10, "usageItems": [{}]}

    result = p.probe(ENV, NOW, Fake())
    assert len(result["checks"]) == 13
    assert result["checks"][-1]["status"] == "reachable_not_evidence"
    assert result["status"] == "BLOCKED"
    assert "metrics" not in result and "plans_observed_at" not in result
    assert "limits_checked_at" not in result
    assert "SECRET" not in json.dumps(result)


def test_cli_always_nonzero_even_with_old_report_available():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/static_ops/probe_providers.py")],
        env={"STATIC_QUOTA_REPORT": '{"plans":{"automatic_billing":false}}'},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert result.stderr == ""
    assert json.loads(result.stdout)["status"] == "BLOCKED"


def test_deep_json_does_not_abort_remaining_probes():
    with pytest.raises(p.ProbeError, match="invalid_json"):
        p.strict_json(b'{"nested":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}")


def test_probe_queries_documented_database_metrics_without_guessing_outbound():
    queries = []

    class Fake:
        def get(self, service, path, query=None, **kwargs):
            if service == "monitoring" and path.endswith("/timeSeries"):
                queries.append(query["filter"])
                return {"timeSeries": [{}]}
            raise p.ProbeError("missing_credential")

    result = p.probe(ENV, NOW, Fake())
    assert len(queries) == 4
    for metric in (
        "document/read_ops_count",
        "document/write_ops_count",
        "document/delete_ops_count",
        "storage/data_and_index_storage_bytes",
    ):
        assert any(f'"firestore.googleapis.com/{metric}"' in query for query in queries)
    assert all('resource.type = "firestore.googleapis.com/Database"' in query for query in queries)
    assert result["status"] == "BLOCKED"


@pytest.mark.parametrize("name", p.CHECKS)
def test_selected_probe_cannot_read_another_provider_or_billing(name):
    calls = []

    class Fake:
        def get(self, service, path, *args, **kwargs):
            calls.append((service, path))
            raise p.ProbeError("http_403")

    result = p.probe(ENV, NOW, Fake(), only=name)
    assert len(calls) == 1
    assert result["checks"] == [{"check": name, "status": "http_403"}]
    assert result["status"] == "BLOCKED"
    if name != "github_billing_usage":
        assert "settings/billing" not in calls[0][1]


def test_unknown_probe_is_rejected_before_network():
    with pytest.raises(p.ProbeError, match="unknown_check"):
        p.probe(ENV, NOW, only="https://arbitrary.test")
