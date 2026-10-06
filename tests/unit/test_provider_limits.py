"""Representative public-document fixtures, not live limit-review receipts."""

import copy
import importlib.util
import io
import sys
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "scripts/static_ops"))
try:
    spec = importlib.util.spec_from_file_location(
        "provider_limits", ROOT / "scripts/static_ops/verify_limits.py"
    )
    limits = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(limits)
finally:
    sys.path.pop(0)

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def table(header, rows):
    def row(values, tag):
        return "<tr>" + "".join(f"<{tag}>{v}</{tag}>" for v in values) + "</tr>"

    return "<table>" + row(header, "th") + "".join(row(r, "td") for r in rows) + "</table>"


def documents():
    return {
        "pages": "<h2>Builds</h2>"
        + table(["", "Free", "Pro", "Business"], [["Builds per month", "500", "5,000", "20,000"]]),
        "workers": "<h2>Account plan limits</h2>"
        + table(
            ["Feature", "Workers Free", "Workers Paid"], [["Requests", "100,000/day", "No limit"]]
        )
        + "<h2>Daily requests</h2><p>Accounts on the Workers Free plan "
        "have a daily request limit of "
        "100,000 requests, resetting at midnight UTC. When a Worker exceeds this limit, "
        "Cloudflare returns Error 1027.</p>",
        "firestore": "<h2>Free quota</h2>"
        + table(
            ["Free tier", "Quota"],
            [
                ["Stored data", "1 GiB"],
                ["Document reads", "50,000 per day"],
                ["Document writes", "20,000 per day"],
                ["Document deletes", "20,000 per day"],
                ["Outbound data transfer", "10 GiB per month"],
            ],
        )
        + "<p>Quotas are applied daily and reset around midnight Pacific time.</p>"
        + "<p>Cloud Firestore allows exactly one free database per project.</p>",
        "github": "<h2>Free use of GitHub Actions</h2>"
        + table(
            [
                "Plan",
                "Artifact storage",
                "Minutes (per month)",
                "Cache storage (per repository)",
                "Custom image storage",
            ],
            [
                [p, "other", "other", "10 GB", "other"]
                for p in (
                    "GitHub Free",
                    "GitHub Pro",
                    "GitHub Free for organizations",
                    "GitHub Team",
                    "GitHub Enterprise Cloud",
                )
            ],
        )
        + "<h2>How storage billing works</h2>"
        + "<p>Shared storage: Actions artifacts and GitHub Packages storage "
        "share the same pooled allowance. See GitHub Packages billing.</p>"
        + "<p>Cache storage: Actions cache storage is a separate allowance "
        "of 10 GB per repository. "
        "Cache storage is not shared with artifacts or GitHub Packages.</p>"
        + "<p>Monthly total: Your bill reflects the total storage used "
        "throughout the month, measured in GB-Hours</p>"
        + "<h3>Storage measurement units</h3><p>1 GB = 2^30 bytes = 1,073,741,824 bytes</p>",
    }


class Client:
    def __init__(self, docs):
        self.docs = docs

    def read(self, name):
        return ("<main>" + self.docs[name] + "</main>").encode()


def test_only_complete_semantic_verification_issues_timestamp_and_digests():
    result = limits.audit(NOW, Client(documents()))
    assert result["status"] == "OK" and result["limits_checked_at"] == NOW.isoformat()
    assert len(result["checks"]) == 4
    assert all(len(check["sha256"]) == 64 for check in result["checks"])
    assert result["limits"]["firestore_storage_bytes"] == 1024**3
    assert result["limits"]["actions_cache_storage_bytes"] == 10 * 1024**3
    assert "actions_storage_gb_hours" not in result["limits"]


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("pages", "500", "501"),
        ("pages", ">Free<", ">Paid<"),
        ("workers", "100,000/day", "200,000/day"),
        ("workers", "midnight UTC", "midnight Pacific"),
        ("firestore", "1 GiB", "2 GiB"),
        ("firestore", "50,000", "60,000"),
        ("firestore", "20,000", "30,000"),
        ("firestore", "per month", "per day"),
        ("firestore", "Pacific", "UTC"),
        ("firestore", "exactly one", "two"),
        ("github", "10 GB", "20 GB"),
        ("github", "2^30", "10^9"),
        ("github", "same pooled allowance", "different allowances"),
        ("github", "not shared", "shared"),
        ("github", "GB-Hours", "bytes"),
    ],
)
def test_changed_limit_unit_scope_or_shared_semantics_cannot_renew(name, old, new):
    docs = documents()
    docs[name] = docs[name].replace(old, new)
    result = limits.audit(NOW, Client(docs))
    assert result["status"] == "BLOCKED"
    assert "limits_checked_at" not in result and "limits" not in result


def test_number_in_wrong_plan_or_script_is_not_free_allowance_evidence():
    docs = documents()
    docs["pages"] = docs["pages"].replace("<td>500</td>", "<td>5</td>") + "<script>500</script>"
    assert limits.audit(NOW, Client(docs))["status"] == "BLOCKED"
    docs["pages"] = "<script>" + documents()["pages"] + "</script>"
    assert limits.audit(NOW, Client(docs))["status"] == "BLOCKED"


def test_ambiguous_table_and_truncated_documents_fail():
    for doc in (documents()["pages"] * 2, documents()["pages"].replace("</table>", "")):
        docs = copy.deepcopy(documents())
        docs["pages"] = doc
        assert limits.audit(NOW, Client(docs))["status"] == "BLOCKED"


def test_transport_is_fixed_public_https_without_auth_and_bounded(monkeypatch):
    requests = []
    response = io.BytesIO(b"<html>text</html>")
    response.status = 200
    response.headers = Message()
    response.headers["Content-Type"] = "text/html; charset=utf-8"

    def open_request(request, **kwargs):
        requests.append(request)
        return response

    client = limits.PublicClient(opener=SimpleNamespace(open=open_request))
    assert client.read("pages") == b"<html>text</html>"
    assert requests[0].full_url == limits.SOURCES["pages"]
    assert requests[0].get_header("Authorization") is None
    assert requests[0].get_header("Cookie") is None
    with pytest.raises(limits.ProbeError, match="unknown_limits_source"):
        client.read("https://other.test")
    monkeypatch.setattr(limits, "MAX_BYTES", 1)
    response = io.BytesIO(b"xx")
    response.status = 200
    response.headers = Message()
    response.headers["Content-Type"] = "text/html"
    with pytest.raises(limits.ProbeError, match="response_too_large"):
        client.read("pages")


def test_failed_reads_never_renew_or_leak_error_payload():
    def fail(*args, **kwargs):
        raise URLError("SECRET-proxy-error")

    result = limits.audit(NOW, limits.PublicClient(opener=SimpleNamespace(open=fail)))
    assert result["status"] == "BLOCKED" and "limits_checked_at" not in result
    assert {check["status"] for check in result["checks"]} == {"transport_failed"}
    assert "SECRET" not in str(result)


@pytest.mark.parametrize(
    "replacement", ["no longer have a", "used to have a", "historically had a"]
)
def test_negative_or_historical_sentence_cannot_renew(replacement):
    docs = documents()
    docs["workers"] = docs["workers"].replace("have a daily", replacement + " daily")
    assert "limits_checked_at" not in limits.audit(NOW, Client(docs))


@pytest.mark.parametrize(
    "wrapper",
    [
        "<div hidden>{}</div>",
        '<div aria-hidden="true">{}</div>',
        '<div style="display: none">{}</div>',
        '<div class="hidden">{}</div>',
        "<del>{}</del>",
        "<s>{}</s>",
        "<template>{}</template>",
    ],
)
def test_hidden_or_deleted_historical_table_cannot_authorize_visible_changed_limit(wrapper):
    docs = documents()
    original = docs["pages"]
    changed = original.replace("500", "100").replace(">Pro<", ">New plan<")
    docs["pages"] = wrapper.format(original) + changed
    assert "limits_checked_at" not in limits.audit(NOW, Client(docs))


def test_clause_or_table_in_historical_section_cannot_renew():
    for name, heading in (("pages", "Builds"), ("workers", "Daily requests")):
        docs = documents()
        docs[name] = docs[name].replace(f"<h2>{heading}</h2>", "<h2>Historical limits</h2>")
        assert "limits_checked_at" not in limits.audit(NOW, Client(docs))


@pytest.mark.parametrize("level", [3, 4, 5, 6])
def test_unreviewed_subsection_cannot_inherit_current_parent_heading(level):
    docs = documents()
    docs["pages"] = docs["pages"].replace(
        "<h2>Builds</h2>", f"<h2>Builds</h2><h{level}>Historical limits</h{level}>"
    )
    assert "limits_checked_at" not in limits.audit(NOW, Client(docs))


def test_partial_http_document_cannot_renew_even_when_body_is_parseable():
    def open_request(request, **kwargs):
        response = io.BytesIO(b"<main></main>")
        response.status = 206
        response.headers = Message()
        response.headers["Content-Type"] = "text/html"
        return response

    result = limits.audit(NOW, limits.PublicClient(opener=SimpleNamespace(open=open_request)))
    assert result["status"] == "BLOCKED" and "limits_checked_at" not in result
    assert {check["status"] for check in result["checks"]} == {"incomplete_document_response"}
