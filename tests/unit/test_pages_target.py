"""One selected GET-only Pages read emits no raw project settings or secret values."""

import copy
import io
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.static_ops import pages_target as target
from scripts.static_ops import probe_providers as probe

NOW = datetime(2026, 10, 9, 1, tzinfo=UTC)
ENV = {"STATIC_CLOUDFLARE_READ_TOKEN": "PRIVATE_TOKEN", "CLOUDFLARE_ACCOUNT_ID": "a" * 32,
       "GITHUB_SHA": "b" * 40}
PROJECT_ID = "c" * 32
PATH = f"/client/v4/accounts/{'a' * 32}/pages/projects/navmate"


def deployment(number=1, stage="deploy", state="success", environment="production"):
    return {
        "id": f"{number:032x}", "project_id": PROJECT_ID, "project_name": "navmate",
        "environment": environment, "uses_functions": False,
        "latest_stage": {"name": stage, "status": state},
        "deployment_trigger": {"metadata": {"commit_hash": "d" * 40, "branch": "main",
                                             "commit_message": "PRIVATE_MESSAGE"}},
        "env_vars": {"SECRET": {"value": "PRIVATE_VALUE"}},
    }


def project():
    return {"id": PROJECT_ID, "name": "navmate", "production_branch": "main",
            "domains": ["navmate.yuto24.com"], "uses_functions": False,
            "canonical_deployment": deployment(),
            "deployment_configs": {"production": {name: {} for name in target.BINDINGS}},
            "build_config": {"web_analytics_token": "PRIVATE_ANALYTICS"}}


def page(rows, number=1, total=None):
    total = len(rows) if total is None else total
    return {"success": True, "result": rows, "result_info": {
        "page": number, "per_page": 20, "count": len(rows), "total_count": total,
        "total_pages": (total + 19) // 20,
    }}


def observe(p=None, pages=None, after=None):
    p = project() if p is None else p
    responses = [{"success": True, "result": p}, *(pages or [page([deployment()])]),
                 {"success": True, "result": p if after is None else after}]
    calls = []

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "GET" and request.data is None
            assert urlsplit(request.full_url).hostname == "api.cloudflare.com"
            calls.append(request.full_url)
            return Response(json.dumps(responses.pop(0)).encode())

    result = probe.probe(ENV, NOW, probe.Client(ENV, opener=Opener()),
                         only="cloudflare_pages_target")
    assert "PRIVATE" not in json.dumps(result)
    assert result["reviewed_sha"] == ENV["GITHUB_SHA"]
    assert result["status"] == "BLOCKED"
    assert not any(key in result for key in ("metrics", "plans", "limits_checked_at"))
    return result["checks"][0], calls


def test_complete_get_only_scope_and_project_reread():
    result, calls = observe()
    assert [urlsplit(url).path for url in calls] == [PATH, PATH + "/deployments", PATH]
    assert parse_qs(urlsplit(calls[1]).query) == {"page": ["1"], "per_page": ["20"]}
    assert result["status"] == "pages_target_observed"
    assert all(result[key] is True for key in (
        "project_matches", "branch_matches", "domain_matches", "project_unchanged",
        "pagination_complete", "production_in_inventory",
    ))
    assert result["production"]["commit"] == "d" * 40
    assert result["in_flight_present"] is False
    assert result["project_uses_functions"] is False
    assert all(value is False for value in result["production_binding_present"].values())


def test_inflight_preview_on_last_page_is_not_missed():
    rows = [deployment(i) for i in range(1, 21)]
    last = deployment(21, "build", "active", "preview")
    result, calls = observe(pages=[page(rows, total=21), page([last], 2, 21)])
    assert len(calls) == 4
    assert result["in_flight_present"] is True
    assert [row["id"] for row in result["in_flight"]] == [last["id"]]


@pytest.mark.parametrize("stage,state,expected", [
    ("queued", "idle", True), ("build", "success", True), ("deploy", "active", True),
    ("deploy", "success", False), ("build", "failure", False),
    ("build", "canceled", False), ("queued", "skipped", False),
    ("PRIVATE_STAGE", "PRIVATE_STATE", "UNKNOWN"), (None, "success", "UNKNOWN"),
])
def test_states_never_treat_unrecognized_or_between_stages_as_idle(stage, state, expected):
    result, _ = observe(pages=[page([deployment(stage=stage, state=state)])])
    assert result["in_flight_present"] == expected


def test_missing_optional_runtime_and_production_metadata_remain_unknown():
    p = project()
    del p["uses_functions"]
    del p["deployment_configs"]
    p["canonical_deployment"] = None
    result, _ = observe(p)
    assert result["production"] is None
    assert result["production_in_inventory"] == "UNKNOWN"
    assert result["project_uses_functions"] == "UNKNOWN"
    assert all(v == "UNKNOWN" for v in result["production_binding_present"].values())


def test_runtime_values_are_only_presence_booleans_not_raw_bindings():
    p = project()
    p["uses_functions"] = True
    p["deployment_configs"]["production"]["browsers"] = {"PRIVATE_NAME": {}}
    p["deployment_configs"]["production"]["env_vars"] = {"SECRET": {"value": "PRIVATE_VALUE"}}
    result, _ = observe(p)
    assert result["project_uses_functions"] is True
    assert result["production_binding_present"]["browsers"] is True
    assert result["production_binding_present"]["env_vars"] is True


def test_project_drift_cannot_claim_idle_even_if_completed_inventory_was_idle():
    after = project()
    after["latest_deployment"] = deployment(2, "queued", "idle")
    result, _ = observe(after=after)
    assert result["status"] == "pages_target_changed"
    assert result["project_unchanged"] is False
    assert result["in_flight_present"] == "UNKNOWN"


@pytest.mark.parametrize("change,expected", [
    (lambda p: p.pop("result_info"), "missing_page_metadata"),
    (lambda p: p["result_info"].update(total_count=2), "incomplete_inventory"),
    (lambda p: p["result"][0].update(project_id="other"), "invalid_deployment_scope"),
    (lambda p: p.update(result=[], result_info={"page": 1, "per_page": 20, "count": 0,
                                               "total_count": 0, "total_pages": 0}), "no_data"),
])
def test_incomplete_or_invalid_lists_do_not_claim_no_inflight(change, expected):
    response = page([deployment()])
    change(response)
    result, _ = observe(pages=[response])
    assert result == {"check": "cloudflare_pages_target", "status": expected}


def test_scope_mismatch_stops_at_project_read():
    p = project()
    p["name"] = "other"
    result, calls = observe(p)
    assert result["status"] == "pages_target_mismatch" and len(calls) == 1


def test_duplicate_and_changed_pagination_are_not_complete():
    rows = [deployment(i) for i in range(1, 21)]
    for response, expected in [
        (page([deployment(1)], 2, 21), "duplicate_inventory_id"),
        (page([deployment(21)], 2, 22), "inventory_changed_during_pagination"),
    ]:
        result, _ = observe(pages=[page(copy.deepcopy(rows), total=21), response])
        assert result["status"] == expected and "in_flight_present" not in result


def test_cli_missing_credentials_fails_without_traceback_or_auth_fallback():
    import sys
    root = Path(__file__).parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/static_ops/probe_providers.py"),
                             "--check", "cloudflare_pages_target"],
                            env={"CLOUDFLARE_ACCOUNT_ID": "a" * 32},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 1 and result.stderr == ""
    assert json.loads(result.stdout)["checks"][0]["status"] == "missing_credential"


def test_inventory_membership_uses_identity_not_optional_fields():
    row = deployment()
    del row["uses_functions"]
    result, _ = observe(pages=[page([row])])
    assert result["production_in_inventory"] is True
    assert result["production"]["uses_functions"] is False


def test_different_deployment_identity_is_not_production_membership():
    result, _ = observe(pages=[page([deployment(2)])])
    assert result["production_in_inventory"] is False


@pytest.mark.parametrize("skipped,state,expected", [
    (True, "idle", "SKIPPED"), (False, "idle", "PENDING"),
    (None, "idle", "PENDING"), ("true", "idle", "PENDING"),
    (False, "active", "ACTIVE"), (None, "active", "ACTIVE"),
    (True, "active", "UNKNOWN"), (False, "skipped", "UNKNOWN"),
    (None, "skipped", "SKIPPED"),
])
def test_explicit_skip_pending_active_and_conflicts(skipped, state, expected):
    row = deployment(stage="queued", state=state)
    row.update(is_skipped=skipped, skip_reason="production_deployments_disabled",
               created_on="2020-01-01T00:00:00Z", modified_on="2020-01-01T00:00:00Z")
    result, calls = observe(pages=[page([row])])
    assert len(calls) == 3
    assert result["activity_counts"][expected] == 1
    records = result["skipped"] if expected == "SKIPPED" else result["in_flight"]
    assert records[0]["activity"] == expected
    assert records[0]["created_on"] == "2020-01-01T00:00:00+00:00"
    assert result["in_flight_present"] == (
        False if expected == "SKIPPED" else "UNKNOWN" if expected == "UNKNOWN" else True
    )


def test_skipped_inventory_is_preserved_across_pages_without_active_inference():
    rows = [deployment(i, "queued", "idle") for i in range(1, 22)]
    for row in rows:
        row.update(is_skipped=True, skip_reason="superseded_queued_build")
    result, calls = observe(pages=[page(rows[:20], total=21), page(rows[20:], 2, 21)])
    assert len(calls) == 4
    assert result["activity_counts"] == {
        "ACTIVE": 0, "PENDING": 0, "SKIPPED": 21, "FINISHED": 0, "UNKNOWN": 0,
    }
    assert len(result["skipped"]) == 21 and result["in_flight"] == []
    assert all(row["skip_reason"] == "superseded_queued_build" for row in result["skipped"])


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-09T09:15:00.123456+09:00", "2026-10-09T00:15:00.123456+00:00"),
    (None, None), ("PRIVATE_TIME", "UNKNOWN"), (123, "UNKNOWN"),
    ("2026-02-30T00:00:00Z", "UNKNOWN"), ("2026-10-09T00:00:00", "UNKNOWN"),
    ("2026-10-09T00:00:00Z PRIVATE", "UNKNOWN"),
    ("0001-01-01T00:00:00+01:00", "UNKNOWN"),
    ("2026-10-09T00:00:00+00:99", "UNKNOWN"),
])
def test_native_timestamp_sanitization_without_clock_substitution(raw, expected):
    row = deployment(stage="queued", state="idle")
    row.update(created_on=raw, modified_on=raw, skip_reason="PRIVATE_REASON")
    row["latest_stage"].update(started_on=raw, ended_on=raw)
    result, _ = observe(pages=[page([row])])
    observed = result["in_flight"][0]
    assert all(observed[k] == expected for k in (
        "created_on", "modified_on", "stage_started_on", "stage_ended_on",
    ))
    assert observed["skip_reason"] == "UNKNOWN"
    assert result["in_flight_present"] is True


def test_missing_timestamps_remain_unknown_and_never_clear_pending():
    result, _ = observe(pages=[page([deployment(stage="queued", state="idle")])])
    assert result["in_flight"][0]["created_on"] == "UNKNOWN"
    assert result["in_flight"][0]["stage_ended_on"] == "UNKNOWN"
    assert result["in_flight_present"] is True


@pytest.mark.parametrize("value,state,present", [
    (None, "null", "UNKNOWN"), ({}, "empty", False),
    ({"PRIVATE_BINDING": {}}, "nonempty", True),
    ([], "invalid", "UNKNOWN"), ("PRIVATE_SHAPE", "invalid", "UNKNOWN"),
    ({"PRIVATE_BINDING": None}, "invalid", "UNKNOWN"),
])
def test_binding_shapes_do_not_expose_values_or_infer_absence(value, state, present):
    p = project()
    for name in target.BINDINGS:
        p["deployment_configs"]["production"][name] = value
    result, _ = observe(p)
    assert set(result["production_binding_state"].values()) == {state}
    assert all(v == present for v in result["production_binding_present"].values())


def test_missing_binding_is_distinct_from_unavailable_parent_configuration():
    p = project()
    p["deployment_configs"]["production"] = {}
    result, _ = observe(p)
    assert result["production_configuration_state"] == "empty"
    assert set(result["production_binding_state"].values()) == {"missing"}
    for value, expected in ((None, "null"), ([], "invalid")):
        p["deployment_configs"]["production"] = value
        result, _ = observe(p)
        assert result["production_configuration_state"] == expected
        assert set(result["production_binding_state"].values()) == {"UNKNOWN"}
        assert result["production_env_metadata"]["other_variable_count"] == "UNKNOWN"


def test_env_metadata_only_known_names_types_and_other_count():
    p = project()
    p["deployment_configs"]["production"]["env_vars"] = {
        "VITE_FIREBASE_PROJECT_ID": {"type": "plain_text", "value": "PRIVATE_PROJECT"},
        "VITE_FIREBASE_API_KEY": {"type": "secret_text", "value": "PRIVATE_SECRET"},
        "VITE_TAF_PROXY_URL": {"type": "PRIVATE_TYPE", "value": "PRIVATE_URL"},
        "VITE_FIREBASE_AUTH_DOMAIN": None,
        "VITE_FIREBASE_APP_ID": {"type": "plain_text", "value": ["PRIVATE_INVALID"]},
        "PRIVATE_OTHER_NAME": {"type": "secret_text", "value": "PRIVATE_VALUE"},
    }
    result, _ = observe(p)
    metadata = result["production_env_metadata"]
    assert metadata["other_variable_count"] == 1
    assert metadata["known_build_variables"] == {
        "VITE_FIREBASE_PROJECT_ID": "plain_text", "VITE_FIREBASE_API_KEY": "secret_text",
        "VITE_TAF_PROXY_URL": "invalid", "VITE_FIREBASE_AUTH_DOMAIN": "invalid",
        "VITE_FIREBASE_APP_ID": "invalid", "VITE_LEGACY_MIGRATION_URL": "missing",
    }


def test_skipped_observation_does_not_hide_project_drift():
    row = deployment(stage="queued", state="idle")
    row["is_skipped"] = True
    after = project()
    after["latest_deployment"] = deployment(2, "queued", "idle")
    result, _ = observe(pages=[page([row])], after=after)
    assert result["activity_counts"]["SKIPPED"] == 1
    assert result["in_flight_present"] == "UNKNOWN"
