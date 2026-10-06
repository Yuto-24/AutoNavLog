"""Composition and redaction contracts; fixtures are never live provider evidence."""

import copy
import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / "scripts/static_ops"))
try:
    gate = load("collection_gate_under_test", ROOT / "scripts/static_ops/collection_gate.py")
finally:
    sys.path.pop(0)
fixtures = load("gate_schema_fixtures", ROOT / "tests/unit/test_static_operations.py")
NOW = fixtures.NOW


def evidence():
    report = fixtures.report(NOW)
    for name, limit in gate.verify_limits.LIMITS.items():
        report["metrics"][name]["limit"] = limit
    limits = {
        "status": "OK",
        "limits": dict(gate.verify_limits.LIMITS),
        "limits_checked_at": NOW.isoformat(),
        "checks": [
            {"source": source, "url": url, "status": "verified", "sha256": "a" * 64}
            for source, url in gate.verify_limits.SOURCES.items()
        ],
    }
    return {
        "plans": report["plans"],
        "plan_observations": {name: NOW.isoformat() for name in report["plans"]},
        "metrics": report["metrics"],
        "scopes": report["scopes"],
        "limits": limits,
        "now": NOW,
    }


def test_complete_nine_metric_collection_passes_both_gates():
    assert gate.assemble(**evidence())["status"] == "OK"
    assert gate.assemble(**evidence(), publication=True)["gate"] == "free-plans-only"


@pytest.mark.parametrize("name", sorted(gate.METRICS))
@pytest.mark.parametrize("publication", [False, True])
def test_every_metric_is_required_even_for_publication(name, publication):
    data = evidence()
    del data["metrics"][name]
    with pytest.raises(ValueError, match="incomplete_metric_collection"):
        gate.assemble(**data, publication=publication)


@pytest.mark.parametrize("field", sorted(gate.PLAN_FIELDS))
@pytest.mark.parametrize("section", ["plans", "plan_observations"])
def test_each_plan_and_its_observation_are_required(field, section):
    data = evidence()
    del data[section][field]
    with pytest.raises(ValueError, match="incomplete_plan_collection"):
        gate.assemble(**data)


@pytest.mark.parametrize("hours", [-37, 1])
def test_one_bad_plan_timestamp_cannot_hide_behind_other_fresh_sources(hours):
    data = evidence()
    data["plan_observations"]["workers"] = (NOW + timedelta(hours=hours)).isoformat()
    with pytest.raises(ValueError):
        gate.assemble(**data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["limits"].update(status="BLOCKED"),
        lambda d: d["limits"]["checks"].pop(),
        lambda d: d["limits"]["checks"][0].update(status="no_data"),
        lambda d: d["limits"]["checks"][0].update(url="https://untrusted.example"),
        lambda d: d["limits"]["checks"][0].update(sha256=""),
        lambda d: d["limits"]["limits"].update(pages_builds=501),
        lambda d: d["metrics"]["pages_builds"].update(limit=501),
        lambda d: d["limits"].update(limits_checked_at=(NOW - timedelta(days=32)).isoformat()),
    ],
)
def test_limits_must_be_semantically_verified_and_fresh(mutation):
    data = evidence()
    mutation(data)
    with pytest.raises(ValueError):
        gate.assemble(**data, publication=True)


def test_publication_preserves_optional_usage_warning_behavior():
    data = evidence()
    data["metrics"]["workers_requests"]["used"] = 100_000
    with pytest.raises(ValueError):
        gate.assemble(**data)
    assert gate.assemble(**data, publication=True)["status"] == "OK"
    data["metrics"]["workers_requests"]["observed_at"] = (NOW - timedelta(hours=37)).isoformat()
    with pytest.raises(ValueError):
        gate.assemble(**data, publication=True)


def test_paid_cache_cannot_be_hidden_by_plan_only_publication():
    data = evidence()
    data["metrics"]["actions_cache_storage_bytes"]["billing"]["billed_amount_usd"] = 0.02
    with pytest.raises(ValueError):
        gate.assemble(**data, publication=True)


def test_assembly_does_not_refresh_any_evidence_timestamp(monkeypatch):
    data = evidence()
    oldest = (NOW - timedelta(hours=5)).isoformat()
    limits_time = (NOW - timedelta(days=10)).isoformat()
    data["plan_observations"]["firebase"] = oldest
    data["limits"]["limits_checked_at"] = limits_time
    before = copy.deepcopy(data)
    captured = []
    original = gate.operations.quota

    def validate(report, *args, **kwargs):
        captured.append(copy.deepcopy(report))
        return original(report, *args, **kwargs)

    monkeypatch.setattr(gate.operations, "quota", validate)
    gate.assemble(**data)
    assert data == before
    assert captured[0]["plans_observed_at"] == oldest
    assert captured[0]["limits_checked_at"] == limits_time
    assert captured[0]["metrics"] == before["metrics"]


@pytest.mark.parametrize("publication", [False, True])
def test_no_report_fallback_or_native_data_export(monkeypatch, publication):
    class NoReportEnv(dict):
        def get(self, key, default=None):
            assert key != "STATIC_QUOTA_REPORT", "legacy report must never be consulted"
            return super().get(key, default)

    monkeypatch.setattr(
        gate.native, "collect_fragments",
        lambda *args: (
            {"PRIVATE-owner-data": "SECRET"}, [{"check": "firebase", "status": "no_data"}],
        ),
    )
    monkeypatch.setattr(gate.verify_limits, "audit", lambda *args: evidence()["limits"])
    result = gate.evaluate(NoReportEnv(STATIC_QUOTA_REPORT="SECRET"), NOW, publication=publication)
    assert result["status"] == "BLOCKED"
    output = json.dumps(result)
    assert "SECRET" not in output and "PRIVATE" not in output
    assert "metrics" not in result and "plans" not in result and "limits_checked_at" not in result
