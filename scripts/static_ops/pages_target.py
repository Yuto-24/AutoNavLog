"""GET-only Pages target observation; never publication or quota authorization."""

import re

try:
    from . import cloudflare_inventory as inventory
except ImportError:
    import cloudflare_inventory as inventory

UNKNOWN = "UNKNOWN"
BINDINGS = (
    "kv_namespaces", "durable_object_namespaces", "d1_databases", "r2_buckets",
    "services", "queue_producers", "analytics_engine_datasets", "ai_bindings",
    "vectorize_bindings", "hyperdrive_bindings", "browsers", "mtls_certificates", "env_vars",
)


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
    return {
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
    }


def in_flight(item):
    if item["state"] in ("failure", "canceled", "skipped"):
        return False
    if item["state"] in ("idle", "active"):
        return True
    if item["state"] == "success" and item["stage"] != UNKNOWN:
        return item["stage"] != "deploy"
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
    runtime = runtime if isinstance(runtime, dict) else {}
    result = {
        "status": "pages_target_observed",
        "account_scope_from_configured_path": True,
        "project_id": project_id,
        "project_matches": matches(project.get("name"), "navmate"),
        "branch_matches": matches(project.get("production_branch"), "main"),
        "domain_matches": "navmate.yuto24.com" in domains
        if isinstance(domains, list) and all(isinstance(d, str) for d in domains) else UNKNOWN,
        "project_uses_functions": boolean(project.get("uses_functions")),
        "production_binding_present": {
            name: bool(runtime[name]) if isinstance(runtime.get(name), dict) else UNKNOWN
            for name in BINDINGS
        },
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
    result["in_flight"] = [row for row in rows if in_flight(row) is not False]
    result["in_flight_present"] = (
        True if any(in_flight(row) is True for row in rows)
        else UNKNOWN if result["in_flight"] else False
    )
    canonical = result["production"]
    if canonical is not None:
        result["production_in_inventory"] = canonical in rows
    else:
        result["production_in_inventory"] = UNKNOWN
    result["project_unchanged"] = project == read_project(client, path)
    if not result["project_unchanged"]:
        result["status"] = "pages_target_changed"
        # Absence in an inventory that changed during the read cannot show an idle target.
        if result["in_flight_present"] is False:
            result["in_flight_present"] = UNKNOWN
    return result
