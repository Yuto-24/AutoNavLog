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
}
NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)


def report():
    metrics = {}
    for name in (
        "pages_builds",
        "workers_requests",
        "firestore_reads",
        "firestore_writes",
        "firestore_deletes",
        "firestore_storage_bytes",
        "firestore_outbound_bytes",
        "actions_storage_bytes",
    ):
        provider = (
            "cloudflare_account"
            if name.startswith(("pages_", "workers_"))
            else "firebase_project"
            if name.startswith("firestore_")
            else "github_owner"
        )
        zone = ops.ZoneInfo("America/Los_Angeles") if provider == "firebase_project" else UTC
        start = NOW.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
        if name in {"pages_builds", "firestore_outbound_bytes"}:
            start = start.replace(day=1)
            end = (start + timedelta(days=32)).replace(day=1)
        else:
            end = start + timedelta(days=1)
        metrics[name] = {
            "used": 1,
            "limit": 100,
            "observed_at": NOW.isoformat(),
            "source": "dashboard",
            "scope": SCOPES[provider],
            "window": "instant"
            if name.endswith("storage_bytes")
            else {"start": start.isoformat(), "end": end.isoformat()},
        }
    return {
        "plans": {
            "cloudflare": "Free",
            "workers": "Free",
            "firebase": "Spark",
            "github_runner": "public-standard",
            "automatic_billing": False,
        },
        "limits_checked_at": NOW.isoformat(),
        "plans_observed_at": NOW.isoformat(),
        "scopes": SCOPES,
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
