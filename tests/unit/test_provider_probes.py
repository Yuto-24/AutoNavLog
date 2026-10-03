"""Offline native-API fixtures; successful probes cannot authorize publication."""

import importlib.util
import io
import json
import subprocess
import sys
from datetime import UTC, datetime
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
        return io.BytesIO(raw)

    return p.Client(ENV, opener=SimpleNamespace(open=open_request))


@pytest.mark.parametrize("raw", [b"null", b"[]", b"invalid", b'{"a":NaN}', b'{"a":1,"a":2}'])
def test_invalid_json_never_becomes_usage(raw):
    with pytest.raises(p.ProbeError):
        client(raw).get("github", "/repos/owner/repo/actions/cache/usage")


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


def test_no_cloudflare_data_is_not_zero():
    raw = {"data": {"viewer": {"accounts": [{"workersInvocationsAdaptive": []}]}}, "errors": None}
    with pytest.raises(p.ProbeError, match="no_data"):
        p.check_workers(client(json.dumps(raw).encode()), "a" * 32, NOW)


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
            client(json.dumps(response).encode()), "firebase-project", "document/read_count", NOW
        )


def test_firestore_query_uses_current_pacific_day_and_project_filter():
    requests = []
    c = client(b'{"timeSeries":[{}]}', requests)
    p.check_series(c, "firebase-project", "document/read_count", NOW)
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(requests[0].full_url).query)
    assert query["interval.startTime"] == ["2026-10-03T00:00:00-07:00"]
    assert 'resource.labels.project_id = "firebase-project"' in query["filter"][0]
    assert query["interval.endTime"] == [NOW.isoformat()]


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
    assert len(result["checks"]) == 11
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
        p.strict_json(b'{"nested":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}')
