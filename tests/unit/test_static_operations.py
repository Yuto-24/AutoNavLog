"""Operational safety gates: rollback races, retention, missing metrics and exhaustion."""

import copy
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "static_operations", Path(__file__).parents[2] / "scripts/static_ops/operations.py"
)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)
SCOPES = {
    "cloudflare_account": "cf-account",
    "firebase_project": "firebase-project",
    "github_owner": "owner",
    "github_repository": "owner/repo",
}
NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def report(now=NOW):
    metrics = {}
    for name in (
        "pages_builds",
        "workers_requests",
        "firestore_reads",
        "firestore_writes",
        "firestore_deletes",
        "firestore_storage_bytes",
        "firestore_outbound_bytes",
        "actions_cache_storage_bytes",
    ):
        provider = (
            "cloudflare_account"
            if name.startswith(("pages_", "workers_"))
            else "firebase_project"
            if name.startswith("firestore_")
            else "github_repository"
        )
        zone = ops.ZoneInfo("America/Los_Angeles") if provider == "firebase_project" else UTC
        start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        if name in {"pages_builds", "firestore_outbound_bytes"}:
            start = start.replace(day=1)
            end = (start + timedelta(days=32)).replace(day=1)
        else:
            end = start + timedelta(days=1)
        metrics[name] = {
            "observation": "counter",
            "unit": "bytes" if name.endswith("_bytes") else "count",
            "used": 1,
            "limit": 100,
            "observed_at": now.isoformat(),
            "source": "dashboard",
            "scope": SCOPES[provider],
            "window": "instant"
            if name.endswith("storage_bytes")
            else {"start": start.isoformat(), "end": end.isoformat()},
        }
    metrics["actions_cache_storage_bytes"].update(
        limit=10 * 1024**3,
        configured_limit=10 * 1024**3,
        configuration_source="cache settings audit",
    )
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    metrics["actions_storage_gb_hours"] = {
        "observation": "accrued_billing",
        "unit": "GB-hours",
        "used": 104.9,
        "billed_amount_usd": 0,
        "observed_at": now.isoformat(),
        "scope": SCOPES["github_owner"],
        "source": "current owner billing storage report",
        "window": {
            "start": start.isoformat(),
            "end": (start + timedelta(days=32)).replace(day=1).isoformat(),
        },
        "shared_allowance": {
            "coverage": "actions_artifacts_and_packages",
            "status": "within_included",
            "billed_amount_usd": 0,
            "source": "current shared allowance audit",
        },
    }
    metrics["actions_cache_storage_bytes"]["billing"] = {
        "window": dict(metrics["actions_storage_gb_hours"]["window"]),
        "billed_amount_usd": 0,
        "source": "current cache storage billing audit",
    }
    return {
        "schema_version": 2,
        "plans": {
            "cloudflare": "Free",
            "workers": "Free",
            "firebase": "Spark",
            "github_runner": "public-standard",
            "automatic_billing": False,
        },
        "limits_checked_at": now.isoformat(),
        "plans_observed_at": now.isoformat(),
        "scopes": dict(SCOPES),
        "metrics": metrics,
    }


def test_quota_complete_and_with_margin():
    assert ops.quota(report(), NOW, SCOPES)["status"] == "OK"


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), True, -1, 80, 100])
def test_quota_unknown_invalid_or_warning_stops_publication(value):
    evidence = report()
    evidence["metrics"]["workers_requests"]["used"] = value
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


def test_quota_stale_missing_paid_and_unattributed():
    original = report()
    for mutate in (
        lambda r: r["metrics"].pop("firestore_storage_bytes"),
        lambda r: r["plans"].update(firebase="Blaze"),
        lambda r: r["metrics"]["pages_builds"].update(source=""),
        lambda r: r["metrics"]["pages_builds"].update(
            observed_at=(NOW - timedelta(hours=37)).isoformat()
        ),
    ):
        evidence = copy.deepcopy(original)
        mutate(evidence)
        with pytest.raises(ValueError):
            ops.quota(evidence, NOW, SCOPES)


def test_rollback_or_unapproved_source_stops_refresh(monkeypatch):
    monkeypatch.setattr(ops, "fetch", lambda *a: json.dumps({"commit": "a" * 40, "dirty": False}))
    assert ops.release("https://example.com", "a" * 40)["commit"] == "a" * 40
    with pytest.raises(ValueError):
        ops.release("https://example.com", "b" * 40)
    with pytest.raises(ValueError):
        ops.release("https://example.com", "main")


def test_restore_expired_catalog_retains_only_verified_recent_assets(monkeypatch, tmp_path):
    payload = b"prepared NPZ bytes verified by producer on reuse"
    digest = hashlib.sha256(payload).hexdigest()
    asset = {
        "file": digest + ".npz",
        "sha256": digest,
        "bytes": len(payload),
        "run": "20260924060000",
    }
    catalog = {
        "schema_version": 1,
        "expires_at": (NOW - timedelta(hours=1)).isoformat(),
        "assets": [asset, {**asset, "run": "20260901000000"}],
    }
    monkeypatch.setattr(
        ops, "fetch", lambda url, *a: json.dumps(catalog) if url.endswith("json") else payload
    )
    assert ops.restore("https://example.com", tmp_path, NOW) == {"retained_assets": 1}
    assert (tmp_path / asset["file"]).read_bytes() == payload
    assert len(json.loads((tmp_path / "catalog.json").read_text())["assets"]) == 1
    with pytest.raises(ValueError, match="freshness"):
        ops.monitor("https://example.com", NOW)
    asset["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="integrity"):
        ops.restore("https://example.com", tmp_path, NOW)
    asset["file"] = "../escape.npz"
    with pytest.raises(ValueError, match="path"):
        ops.restore("https://example.com", tmp_path, NOW)


def test_monitor_warns_before_expiry(monkeypatch):
    catalog = {
        "assets": [{}],
        "generated_at": (NOW - timedelta(hours=1)).isoformat(),
        "expires_at": (NOW + timedelta(hours=3)).isoformat(),
    }
    monkeypatch.setattr(ops, "fetch", lambda *a: json.dumps(catalog))
    assert ops.monitor("https://example.com", NOW)["status"] == "OK"
    catalog["expires_at"] = (NOW + timedelta(minutes=119)).isoformat()
    with pytest.raises(ValueError):
        ops.monitor("https://example.com", NOW)


@pytest.mark.parametrize(
    "url", ["http://example.com", "https://a:b@example.com", "https://example.com?token=x"]
)
def test_fetch_rejects_unsafe_urls_before_network(url):
    with pytest.raises(ValueError, match="HTTPS"):
        ops.fetch(url)


def test_retained_only_renewal_is_not_publishable(monkeypatch, tmp_path):
    import subprocess

    refresh_spec = importlib.util.spec_from_file_location(
        "refresh_feed", Path(__file__).parents[2] / "scripts/static_ops/refresh_feed.py"
    )
    module = importlib.util.module_from_spec(refresh_spec)
    refresh_spec.loader.exec_module(module)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=json.dumps({"runs": []})),
    )
    with pytest.raises(ValueError, match="No newly prepared"):
        module.refresh(tmp_path / "producer.py", tmp_path / "feed", tmp_path / "cache")


def test_quota_rejects_different_account_and_previous_period():
    evidence = report()
    evidence["metrics"]["pages_builds"]["scope"] = "another-account"
    with pytest.raises(ValueError, match="scope"):
        ops.quota(evidence, NOW, SCOPES)
    evidence = report()
    evidence["metrics"]["workers_requests"]["window"]["start"] = "2026-08-24T00:00:00Z"
    with pytest.raises(ValueError, match="window"):
        ops.quota(evidence, NOW, SCOPES)


def test_optional_quota_does_not_stop_feed_but_unknown_plan_does():
    evidence = report()
    evidence["metrics"]["firestore_reads"]["used"] = 100
    with pytest.raises(ValueError, match="80%"):
        ops.quota(evidence, NOW, SCOPES)
    assert ops.quota(evidence, NOW, SCOPES, publication=True)["gate"] == "free-plans-only"
    evidence["plans"]["automatic_billing"] = True
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES, publication=True)


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_fetch_never_follows_redirect_even_to_matching_inventory(monkeypatch, code):
    def opener(handler):
        def open_url(request, **kwargs):
            assert request.get_header("Cache-control") == "no-cache"
            handler().redirect_request(
                request, None, code, "redirect", {}, "https://other.example/"
            )
            pytest.fail("Redirect must not be followed")

        from types import SimpleNamespace

        return SimpleNamespace(open=open_url)

    monkeypatch.setattr(ops, "build_opener", opener)
    with pytest.raises(ValueError, match="redirected"):
        ops.fetch("https://example.com/release.json")


def unobservable_report(now=NOW):
    evidence = report(now)
    evidence["metrics"]["firestore_outbound_bytes"].update(
        observation="provider_unobservable",
        used=None,
        limit=10 * 1024**3,
        reason="spark_no_usage_counter",
        source="Spark Usage/Monitoring/Quotas audit",
    )
    return evidence


def test_spark_unobservable_is_explicit_and_can_return_to_numeric_monitoring():
    evidence = unobservable_report()
    result = ops.quota(evidence, NOW, SCOPES)
    assert result["status"] == "OK"
    assert result["unobservable"] == ["firestore_outbound_bytes"]
    metric = evidence["metrics"]["firestore_outbound_bytes"]
    assert metric["used"] is None
    metric.update(observation="counter", used=metric["limit"] * 0.8)
    del metric["reason"]
    with pytest.raises(ValueError, match="80%"):
        ops.quota(evidence, NOW, SCOPES)
    metric["used"] = 0
    assert ops.quota(evidence, NOW, SCOPES)["unobservable"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("used", 0),
        ("used", False),
        ("limit", 10_000_000_000),
        ("limit", None),
        ("reason", "permission_denied"),
        ("reason", "unknown"),
        ("reason", None),
        ("source", ""),
        ("source", []),
        ("source", True),
        ("source", "REPLACE_WITH_SOURCE"),
        ("source", "  "),
        ("scope", "other"),
        ("unit", "GiB"),
        ("observed_at", "2026-09-22T12:00:00Z"),
        ("observed_at", "2026-09-25T12:00:00Z"),
        ("observed_at", "2026-09-24T12:00:00"),
        ("window", "instant"),
    ],
)
def test_spark_exception_rejects_fabricated_or_unattributed_evidence(field, value):
    evidence = unobservable_report()
    evidence["metrics"]["firestore_outbound_bytes"][field] = value
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "name",
    [
        "pages_builds",
        "workers_requests",
        "firestore_reads",
        "firestore_writes",
        "firestore_deletes",
        "firestore_storage_bytes",
        "actions_cache_storage_bytes",
        "actions_storage_gb_hours",
    ],
)
def test_unobservable_is_not_a_general_unknown_escape(name):
    evidence = unobservable_report()
    evidence["metrics"][name].update(observation="provider_unobservable", used=None)
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "name",
    [
        "pages_builds",
        "workers_requests",
        "firestore_reads",
        "firestore_writes",
        "firestore_deletes",
        "firestore_storage_bytes",
        "firestore_outbound_bytes",
        "actions_cache_storage_bytes",
    ],
)
@pytest.mark.parametrize(
    "field,value",
    [
        ("used", None),
        ("used", True),
        ("used", float("nan")),
        ("used", float("inf")),
        ("used", -1),
        ("used", 80),
        ("limit", 0),
        ("limit", False),
        ("limit", "100"),
        ("scope", "wrong"),
        ("source", {}),
        ("observed_at", None),
        ("observed_at", "2026-09-22T12:00:00Z"),
        ("window", {}),
    ],
)
def test_all_numeric_counters_fail_closed(name, field, value):
    evidence = report()
    evidence["metrics"][name][field] = (
        evidence["metrics"][name]["limit"] * 0.8 if field == "used" and value == 80 else value
    )
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("used", None),
        ("used", False),
        ("used", -1),
        ("used", float("inf")),
        ("unit", "bytes"),
        ("unit", "GB-months"),
        ("window", "instant"),
        ("observation", "counter"),
        ("billed_amount_usd", 0.01),
        ("billed_amount_usd", -1),
        ("billed_amount_usd", None),
        ("billed_amount_usd", False),
        ("billed_amount_usd", "0"),
        ("billed_amount_usd", float("nan")),
        ("shared_allowance", None),
        ("shared_allowance", {}),
        ("observed_at", "2026-09-22T12:00:00Z"),
        ("scope", "owner/repo"),
    ],
)
def test_accrued_billing_rejects_semantic_mismatch_and_nonzero_bill(field, value):
    evidence = report()
    evidence["metrics"]["actions_storage_gb_hours"][field] = value
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("coverage", "actions_only"),
        ("coverage", "cache"),
        ("status", "unknown"),
        ("status", "credits"),
        ("status", "exceeded"),
        ("source", ""),
        ("billed_amount_usd", 1),
        ("billed_amount_usd", False),
        ("billed_amount_usd", None),
    ],
)
def test_zero_actions_bill_alone_does_not_prove_shared_free_allowance(field, value):
    evidence = report()
    evidence["metrics"]["actions_storage_gb_hours"]["shared_allowance"][field] = value
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("scope", "owner"),
        ("scope", "owner/another-repo"),
        ("configured_limit", 11 * 1024**3),
        ("configured_limit", 0),
        ("configured_limit", None),
        ("configuration_source", ""),
    ],
)
def test_cache_is_separate_and_cannot_enable_paid_capacity(field, value):
    evidence = report()
    evidence["metrics"]["actions_cache_storage_bytes"][field] = value
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.pop("schema_version"),
        lambda r: r.update(schema_version=True),
        lambda r: r.update(schema_version=1),
        lambda r: r.update(schema_version=2.0),
        lambda r: r["metrics"].update(actions_storage_bytes={}),
    ],
)
def test_old_monitor_reports_have_explicit_migration_error(mutate):
    evidence = report()
    mutate(evidence)
    with pytest.raises(ValueError, match="schema.*2"):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 1, 0, tzinfo=UTC),  # UTC rolled over; Pacific is still September.
        datetime(2026, 11, 1, 12, tzinfo=UTC),  # Pacific 25-hour DST reset day.
        datetime(2026, 3, 8, 12, tzinfo=UTC),  # Pacific 23-hour DST reset day.
        datetime(2027, 1, 1, 12, tzinfo=UTC),
    ],
)
def test_current_provider_periods_at_month_year_and_dst_boundaries(now):
    assert ops.quota(unobservable_report(now), now, SCOPES)["status"] == "OK"


@pytest.mark.parametrize("name", ["actions_storage_gb_hours", "firestore_outbound_bytes"])
def test_fresh_observation_from_previous_month_is_not_current_evidence(name):
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    evidence = unobservable_report(now)
    metric = evidence["metrics"][name]
    metric["observed_at"] = "2026-09-30T12:00:00Z"
    with pytest.raises(ValueError, match="another quota period"):
        ops.quota(evidence, now, SCOPES)
    metric["observed_at"] = now.isoformat()
    metric["window"] = {"start": "2026-09-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}
    with pytest.raises(ValueError, match="window"):
        ops.quota(evidence, now, SCOPES)


def test_publication_preserves_legacy_plan_only_report_during_migration():
    evidence = report()
    del evidence["schema_version"], evidence["metrics"], evidence["scopes"]["github_repository"]
    assert ops.quota(evidence, NOW, SCOPES, publication=True)["gate"] == "free-plans-only"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["plans"].update(automatic_billing=0),
        lambda r: r["plans"].update(automatic_billing=True),
        lambda r: r["plans"].update(firebase="Blaze"),
        lambda r: r["plans"].update(github_runner="private"),
        lambda r: r["scopes"].update(firebase_project="another-project"),
        lambda r: r.update(plans_observed_at="2026-09-22T12:00:00Z"),
        lambda r: r.update(limits_checked_at="2026-08-01T12:00:00Z"),
    ],
)
@pytest.mark.parametrize("publication", [False, True])
def test_plan_gate_cannot_be_bypassed_by_unobservable_usage(mutate, publication):
    evidence = unobservable_report()
    mutate(evidence)
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES, publication=publication)


@pytest.mark.parametrize("evidence", [None, [], {}, {"plans": None}])
def test_malformed_report_is_a_validation_error(evidence):
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)


def test_each_required_field_and_metric_is_fail_closed():
    original = unobservable_report()
    for name, metric in original["metrics"].items():
        evidence = copy.deepcopy(original)
        del evidence["metrics"][name]
        with pytest.raises(ValueError):
            ops.quota(evidence, NOW, SCOPES)
        for field in metric:
            evidence = copy.deepcopy(original)
            del evidence["metrics"][name][field]
            with pytest.raises(ValueError):
                ops.quota(evidence, NOW, SCOPES)


def test_quota_cli_monitor_environment_and_failure_exit(monkeypatch, capsys):
    import sys

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", SCOPES["cloudflare_account"])
    monkeypatch.setenv("VITE_FIREBASE_PROJECT_ID", SCOPES["firebase_project"])
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", SCOPES["github_owner"])
    monkeypatch.setenv("GITHUB_REPOSITORY", SCOPES["github_repository"])
    monkeypatch.setattr(sys, "argv", ["operations.py", "quota"])
    evidence = unobservable_report(datetime.now(UTC))
    monkeypatch.setenv("STATIC_QUOTA_REPORT", json.dumps(evidence))
    ops.main()
    assert json.loads(capsys.readouterr().out)["unobservable"] == ["firestore_outbound_bytes"]
    evidence["metrics"]["actions_storage_gb_hours"]["billed_amount_usd"] = 1
    monkeypatch.setenv("STATIC_QUOTA_REPORT", json.dumps(evidence))
    with pytest.raises(ValueError, match="nonzero"):
        ops.main()


def test_cache_cannot_relabel_paid_capacity_as_free_allowance():
    evidence = report()
    evidence["metrics"]["actions_cache_storage_bytes"].update(
        limit=20 * 1024**3,
        configured_limit=20 * 1024**3,
        used=1024**3,
    )
    with pytest.raises(ValueError, match="free allowance"):
        ops.quota(evidence, NOW, SCOPES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("billed_amount_usd", 0.01),
        ("billed_amount_usd", None),
        ("billed_amount_usd", False),
        ("billed_amount_usd", float("nan")),
        ("source", ""),
        ("window", "instant"),
        ("window", {"start": "2026-08-01T00:00:00Z", "end": "2026-09-01T00:00:00Z"}),
    ],
)
def test_small_current_cache_cannot_hide_accrued_charges(field, value):
    evidence = report()
    evidence["metrics"]["actions_cache_storage_bytes"]["billing"][field] = value
    with pytest.raises(ValueError):
        ops.quota(evidence, NOW, SCOPES)
