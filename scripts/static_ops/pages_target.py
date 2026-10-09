"""GET-only Pages target observation; never publication or quota authorization."""

import re
from datetime import UTC, datetime

try:
    from . import cloudflare_inventory as inventory
    from .publication_gate import CONFIGURATION
except ImportError:
    import cloudflare_inventory as inventory
    from publication_gate import CONFIGURATION

UNKNOWN = "UNKNOWN"
BINDINGS = (
    "kv_namespaces", "durable_object_namespaces", "d1_databases", "r2_buckets",
    "services", "queue_producers", "analytics_engine_datasets", "ai_bindings",
    "vectorize_bindings", "hyperdrive_bindings", "browsers", "mtls_certificates", "env_vars",
)
SKIP_REASONS = (
    "commit_message", "preview_deployments_disabled", "production_deployments_disabled",
    "path_config", "branch_config", "pages_to_workers_conversion", "superseded_queued_build",
)
ACTIVITIES = ("ACTIVE", "PENDING", "SKIPPED", "FINISHED", UNKNOWN)


def timestamp(row, key):
    """Normalize provider timestamps to UTC; never invent missing or invalid times."""
    if key not in row:
        return UNKNOWN
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value
    ):
        return UNKNOWN
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
    except (ValueError, OverflowError):
        return UNKNOWN


def object_state(parent, key):
    """Describe an object's shape without exposing keys or values."""
    if not isinstance(parent, dict):
        return UNKNOWN
    if key not in parent:
        return "missing"
    value = parent[key]
    if value is None:
        return "null"
    if not isinstance(value, dict):
        return "invalid"
    return "nonempty" if value else "empty"


def binding_state(runtime, key):
    """Check map-of-objects shape, not resource validity or publication eligibility."""
    state = object_state(runtime, key)
    if state == "nonempty" and any(not isinstance(v, dict) for v in runtime[key].values()):
        return "invalid"
    return state


def env_metadata(runtime):
    """Expose only fixed build-variable names/types and a count of other names, never values."""
    state = object_state(runtime, "env_vars")
    variables = runtime["env_vars"] if state in ("empty", "nonempty") else None
    known = {}
    for name in CONFIGURATION:
        kind = UNKNOWN
        if variables is not None:
            if name not in variables:
                kind = "missing"
            else:
                entry = variables[name]
                kind = entry.get("type") if isinstance(entry, dict) else None
                if (kind not in ("plain_text", "secret_text")
                        or not isinstance(entry.get("value"), str)):
                    kind = "invalid"
        known[name] = kind
    return {"known_build_variables": known,
            "other_variable_count": sum(name not in CONFIGURATION for name in variables)
            if variables is not None else UNKNOWN}


def identifier(value, pattern=r"[a-f0-9-]{8,64}"):
    return value if isinstance(value, str) and re.fullmatch(pattern, value) else UNKNOWN


def boolean(value):
    return value if type(value) is bool else UNKNOWN


def matches(value, expected):
    return value == expected if isinstance(value, str) else UNKNOWN


def deployment(row, project_id):
    """Project scope is validated privately; only allowlisted identity/state leaves memory."""
    if not isinstance(row, dict):
        return None
    if row.get("project_id") != project_id or row.get("project_name") != "navmate":
        inventory.fail("invalid_deployment_scope")
    deployment_id = identifier(row.get("id"))
    if deployment_id == UNKNOWN:
        inventory.fail("invalid_deployment_identity")
    stage = row.get("latest_stage")
    stage = stage if isinstance(stage, dict) else {}
    trigger = row.get("deployment_trigger")
    metadata = trigger.get("metadata") if isinstance(trigger, dict) else None
    metadata = metadata if isinstance(metadata, dict) else {}
    state = stage.get("status")
    name = stage.get("name")
    item = {
        "id": deployment_id,
        "commit": identifier(metadata.get("commit_hash"), r"[a-f0-9]{40}"),
        "branch_matches": matches(metadata.get("branch"), "main"),
        "environment": row.get("environment")
        if row.get("environment") in ("production", "preview") else UNKNOWN,
        "stage": name if name in ("queued", "initialize", "clone_repo", "build", "deploy")
        else UNKNOWN,
        "state": state if state in ("success", "idle", "active", "failure", "canceled", "skipped")
        else UNKNOWN,
        "uses_functions": boolean(row.get("uses_functions")),
        "is_skipped": boolean(row.get("is_skipped")),
        "skip_reason": row.get("skip_reason")
        if row.get("skip_reason") in SKIP_REASONS else UNKNOWN,
        "created_on": timestamp(row, "created_on"),
        "modified_on": timestamp(row, "modified_on"),
        "stage_started_on": timestamp(stage, "started_on"),
        "stage_ended_on": timestamp(stage, "ended_on"),
    }
    item["activity"] = activity(item)
    return item


def activity(item):
    """Separate explicit skip, active and pending observations without using age as evidence."""
    if item["is_skipped"] is True:
        return UNKNOWN if item["state"] == "active" else "SKIPPED"
    if item["state"] == "skipped":
        return UNKNOWN if item["is_skipped"] is False else "SKIPPED"
    if item["state"] == "active":
        return "ACTIVE"
    if (item["state"] in ("failure", "canceled")
            or (item["state"] == "success" and item["stage"] == "deploy")):
        return "FINISHED"
    if (item["state"] == "idle"
            or (item["state"] == "success" and item["stage"] != UNKNOWN)):
        return "PENDING"
    return UNKNOWN


def in_flight(item):
    """Keep pending and uncertain records in the possible-competition inventory."""
    if item["activity"] in ("FINISHED", "SKIPPED"):
        return False
    if item["activity"] in ("PENDING", "ACTIVE"):
        return True
    return UNKNOWN


def read_project(client, path):
    response = client.get("cloudflare", path)
    if response.get("success") is not True or not isinstance(response.get("result"), dict):
        inventory.fail("invalid_project_response")
    return response["result"]


def inspect(client, account):
    """Read project, complete retained deployment list, then project again for observed drift."""
    path = f"/client/v4/accounts/{account}/pages/projects/navmate"
    project = read_project(client, path)
    project_id = identifier(project.get("id"))
    if project_id == UNKNOWN:
        inventory.fail("invalid_project_identity")
    domains = project.get("domains")
    configs = project.get("deployment_configs")
    runtime = configs.get("production") if isinstance(configs, dict) else None
    configuration_state = (
        object_state(configs, "production") if isinstance(configs, dict)
        else object_state(project, "deployment_configs")
    )
    binding_states = {name: binding_state(runtime, name) for name in BINDINGS}
    result = {
        "status": "pages_target_observed",
        "account_scope_from_configured_path": True,
        "project_id": project_id,
        "project_matches": matches(project.get("name"), "navmate"),
        "branch_matches": matches(project.get("production_branch"), "main"),
        "domain_matches": "navmate.yuto24.com" in domains
        if isinstance(domains, list) and all(isinstance(d, str) for d in domains) else UNKNOWN,
        "project_uses_functions": boolean(project.get("uses_functions")),
        "production_configuration_state": configuration_state,
        "production_binding_state": binding_states,
        "production_binding_present": {
            name: state == "nonempty" if state in ("empty", "nonempty") else UNKNOWN
            for name, state in binding_states.items()
        },
        "production_env_metadata": env_metadata(runtime),
        "production": deployment(project.get("canonical_deployment"), project_id),
        "pagination_complete": False,
        "project_unchanged": UNKNOWN,
        "in_flight_present": UNKNOWN,
        "in_flight": [],
    }
    if result["project_matches"] is not True:
        result["status"] = "pages_target_mismatch"
        return result
    rows = inventory.paginated(client, path + "/deployments",
                               lambda row: deployment(row, project_id))
    result["pagination_complete"] = True
    result["activity_counts"] = {name: sum(row["activity"] == name for row in rows)
                                 for name in ACTIVITIES}
    result["skipped"] = [row for row in rows if row["activity"] == "SKIPPED"]
    result["in_flight"] = [row for row in rows if in_flight(row) is not False]
    result["in_flight_present"] = (
        True if any(in_flight(row) is True for row in rows)
        else UNKNOWN if result["in_flight"] else False
    )
    canonical = result["production"]
    if canonical is not None:
        result["production_in_inventory"] = any(row["id"] == canonical["id"] for row in rows)
    else:
        result["production_in_inventory"] = UNKNOWN
    result["project_unchanged"] = project == read_project(client, path)
    if not result["project_unchanged"]:
        result["status"] = "pages_target_changed"
        # Absence in an inventory that changed during the read cannot show an idle target.
        if result["in_flight_present"] is False:
            result["in_flight_present"] = UNKNOWN
    return result
