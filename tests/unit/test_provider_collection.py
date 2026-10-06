"""Native API fixtures, never account recordings or publication evidence."""

import copy
import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
# Give the directly executable sibling import the same search path as its CLI.
sys.path.insert(0, str(ROOT / "scripts/static_ops"))
try:
    spec = importlib.util.spec_from_file_location(
        "provider_collection", ROOT / "scripts/static_ops/collect_providers.py"
    )
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
finally:
    sys.path.pop(0)

NOW = datetime(2026, 10, 3, 7, 2, tzinfo=UTC)
IDENTITY = {
    "project": "firebase-project",
    "number": "123456789",
    "database": "(default)",
    "location": "nam5",
}


class Client:
    def __init__(self, responses):
        self.responses = copy.deepcopy(responses)
        self.calls = []

    def get(self, service, path, query=None):
        self.calls.append((service, path, copy.deepcopy(query)))
        return self.responses.pop(0)


def identity_responses():
    return [
        {
            "name": "projects/123456789",
            "projectId": "firebase-project",
            "projectNumber": "123456789",
            "state": "ACTIVE",
        },
        {"projectId": "firebase-project", "billingEnabled": False},
        {
            "databases": [
                {
                    "name": "projects/firebase-project/databases/(default)",
                    "freeTier": True,
                    "type": "FIRESTORE_NATIVE",
                    "locationId": "nam5",
                }
            ]
        },
    ]


def series(name="firestore_deletes", now=NOW):
    metric, _, label, categories = c.METRICS[name]
    storage = name == "firestore_storage_bytes"
    start = now.astimezone(c.PACIFIC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = []
    for category in sorted(categories, key=str):
        points = []
        for minute in (1, 2):
            interval = {"endTime": (start + timedelta(minutes=minute)).isoformat()}
            if not storage:
                interval["startTime"] = (start + timedelta(minutes=minute - 1)).isoformat()
            points.append({"interval": interval, "value": {"int64Value": str(minute)}})
        rows.append(
            {
                "metric": {
                    "type": f"firestore.googleapis.com/{metric}",
                    "labels": {label: category} if label else {},
                },
                "resource": {
                    "type": "firestore.googleapis.com/Database",
                    "labels": {
                        "resource_container": "firebase-project",
                        "database_id": "(default)",
                        "location": "nam5",
                    },
                },
                "metricKind": "GAUGE" if storage else "DELTA",
                "valueType": "INT64",
                "unit": "By" if storage else "1",
                "points": list(reversed(points)),
            }
        )
    return {"timeSeries": rows}


def test_plan_requires_live_identity_billing_and_explicit_free_database():
    client = Client(identity_responses())
    result = c.firebase_identity(client, "firebase-project", NOW)
    assert result["plan"] == "Spark"
    assert result["database"] == "(default)"
    assert result["observed_at"] == NOW.isoformat()
    assert [call[0] for call in client.calls] == ["firebase", "billing", "firestore"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r[0].update(projectId="another-project"),
        lambda r: r[0].update(state="DELETED"),
        lambda r: r[0].pop("projectNumber"),
        lambda r: r[1].update(billingEnabled=True),
        lambda r: r[1].pop("billingEnabled"),
        lambda r: r[2]["databases"][0].update(freeTier=False),
        lambda r: r[2]["databases"][0].pop("freeTier"),
        lambda r: r[2]["databases"][0].update(name="projects/other/databases/(default)"),
        lambda r: r[2].update(unreachable=["SECRET-region"]),
        lambda r: r[2]["databases"].append(copy.deepcopy(r[2]["databases"][0])),
    ],
)
def test_unknown_paid_or_wrong_database_cannot_become_spark(mutation):
    responses = identity_responses()
    mutation(responses)
    with pytest.raises(c.api.ProbeError):
        c.firebase_identity(Client(responses), "firebase-project", NOW)


@pytest.mark.parametrize(
    "name,used",
    [
        ("firestore_reads", 6),
        ("firestore_writes", 6),
        ("firestore_deletes", 3),
        ("firestore_storage_bytes", 2),
    ],
)
def test_native_delta_sum_and_latest_gauge_keep_provider_observation(name, used):
    client = Client([series(name)])
    result = c.firestore_metric(client, IDENTITY, name, NOW + timedelta(minutes=5))
    assert result["used"] == used
    assert c.timestamp(result["observed_at"]) == NOW
    assert result["scope"] == "firebase-project"
    assert "resource.labels.resource_container" in client.calls[0][2]["filter"]
    assert "resource.labels.project_id" not in client.calls[0][2]["filter"]
    assert "limit" in result
    assert "limits_checked_at" not in result


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["timeSeries"][0]["resource"]["labels"].update(database_id="other"),
        lambda r: r["timeSeries"][0]["resource"]["labels"].update(resource_container="other"),
        lambda r: r["timeSeries"][0].update(metricKind="CUMULATIVE"),
        lambda r: r["timeSeries"][0].update(unit="By"),
        lambda r: r["timeSeries"][0]["points"][0]["value"].update(int64Value="-1"),
        lambda r: r["timeSeries"][0]["points"][0]["value"].update(int64Value=True),
        lambda r: r["timeSeries"][0]["points"].pop(),
        lambda r: r["timeSeries"][0]["points"].append(r["timeSeries"][0]["points"][0]),
        lambda r: r.update(executionErrors=[{"message": "SECRET"}]),
        lambda r: r.update(timeSeries=[]),
    ],
)
def test_invalid_or_incomplete_usage_never_becomes_zero(mutation):
    response = series()
    mutation(response)
    with pytest.raises(c.api.ProbeError):
        c.firestore_metric(Client([response]), IDENTITY, "firestore_deletes", NOW)


def test_missing_operation_class_is_not_implicitly_zero():
    response = series("firestore_reads")
    response["timeSeries"].pop()
    with pytest.raises(c.api.ProbeError, match="incomplete_operation_classes"):
        c.firestore_metric(Client([response]), IDENTITY, "firestore_reads", NOW)


def test_full_pagination_and_duplicate_tokens():
    response = series()
    row = response["timeSeries"][0]
    first, second = copy.deepcopy(row), copy.deepcopy(row)
    first["points"], second["points"] = [row["points"][0]], [row["points"][1]]
    client = Client(
        [
            {"timeSeries": [first], "nextPageToken": "PRIVATE-TOKEN"},
            {"timeSeries": [second]},
        ]
    )
    result = c.firestore_metric(client, IDENTITY, "firestore_deletes", NOW)
    assert result["used"] == 3
    assert client.calls[1][2]["pageToken"] == "PRIVATE-TOKEN"
    assert "PRIVATE" not in json.dumps(result)
    with pytest.raises(c.api.ProbeError, match="repeated_page_token"):
        c.pages(Client([{"rows": [], "nextPageToken": "X"}] * 2), "monitoring", "/p", "rows")


@pytest.mark.parametrize(
    "date,hours",
    [(datetime(2026, 3, 8, 8, 2, tzinfo=UTC), 23), (datetime(2026, 11, 1, 7, 2, tzinfo=UTC), 25)],
)
def test_daily_window_preserves_pacific_dst(date, hours):
    result = c.firestore_metric(Client([series(now=date)]), IDENTITY, "firestore_deletes", date)
    window = result["window"]
    assert (
        c.timestamp(window["end"]) - c.timestamp(window["start"])
    ).total_seconds() == hours * 3600


def test_cache_fragment_does_not_invent_billing_or_shared_allowance():
    result = c.github_cache(
        Client(
            [
                {"full_name": "owner/repo", "active_caches_size_in_bytes": 123},
                {"max_cache_size_gb": 10},
            ]
        ),
        "owner/repo",
        NOW,
    )
    assert result["used"] == 123 and result["configured_limit"] == 10 * 1024**3
    assert "billing" not in result and "shared_allowance" not in result
    with pytest.raises(c.api.ProbeError, match="exceeds_free"):
        c.github_cache(
            Client(
                [
                    {"full_name": "owner/repo", "active_caches_size_in_bytes": 0},
                    {"max_cache_size_gb": 11},
                ]
            ),
            "owner/repo",
            NOW,
        )


def test_cli_without_credentials_fails_closed_without_old_report_or_traceback():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/static_ops/collect_providers.py")],
        env={"STATIC_QUOTA_REPORT": "SECRET-OLD-REPORT"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1 and result.stderr == ""
    body = json.loads(result.stdout)
    assert body["status"] == "BLOCKED"
    assert "metrics" not in body and "limits_checked_at" not in body
    assert "SECRET" not in result.stdout


def test_native_fragments_fit_schema_v2_but_cache_without_billing_fails():
    fixture_spec = importlib.util.spec_from_file_location(
        "static_operations_fixtures", ROOT / "tests/unit/test_static_operations.py"
    )
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    report = fixture.report(NOW)
    for name in c.METRICS:
        report["metrics"][name] = c.firestore_metric(Client([series(name)]), IDENTITY, name, NOW)
    assert fixture.ops.quota(report, NOW, fixture.SCOPES, collected=True)["status"] == "OK"
    report["metrics"]["actions_cache_storage_bytes"] = c.github_cache(
        Client(
            [
                {"full_name": "owner/repo", "active_caches_size_in_bytes": 0},
                {"max_cache_size_gb": 10},
            ]
        ),
        "owner/repo",
        NOW,
    )
    with pytest.raises(ValueError):
        fixture.ops.quota(report, NOW, fixture.SCOPES, publication=True, collected=True)


def test_allowed_older_native_daily_observation_is_not_relabelled_as_now():
    observed = NOW
    collected = NOW + timedelta(hours=12)
    metric = c.firestore_metric(Client([series()]), IDENTITY, "firestore_deletes", collected)
    assert c.timestamp(metric["observed_at"]) == observed
    assert metric["used"] == 3


def test_collection_summary_does_not_export_native_usage_or_project_identity():
    env = {
        "VITE_FIREBASE_PROJECT_ID": "firebase-project",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REPOSITORY_OWNER": "owner",
    }
    responses = (
        identity_responses()
        + [series(name) for name in c.METRICS]
        + [
            {"full_name": "owner/repo", "active_caches_size_in_bytes": 123456789},
            {"max_cache_size_gb": 10},
        ]
    )
    result = c.collect(env, NOW, Client(responses))
    assert {check["status"] for check in result["checks"]} == {
        "fragment_collected_not_complete_report"
    }
    assert result["status"] == "BLOCKED"
    assert "123456789" not in json.dumps(result) and "firebase-project" not in json.dumps(result)
    assert "metrics" not in result and "limits_checked_at" not in result


@pytest.mark.parametrize("native_repository", [None, True, "another/repo", "owner/another"])
def test_cache_response_repository_must_match_before_reading_configuration(native_repository):
    client = Client([{"full_name": native_repository, "active_caches_size_in_bytes": 0}])
    with pytest.raises(c.api.ProbeError, match="invalid_cache_repository"):
        c.github_cache(client, "owner/repo", NOW)
    assert len(client.calls) == 1


def test_cache_response_repository_comparison_matches_github_case_insensitive_names():
    client = Client(
        [
            {"full_name": "Owner/Repo", "active_caches_size_in_bytes": 0},
            {"max_cache_size_gb": 10},
        ]
    )
    assert c.github_cache(client, "owner/repo", NOW)["scope"] == "owner/repo"
