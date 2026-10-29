"""Manual one-way schema evidence; automatic/default updates remain fail closed."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_static_app_release import NEW, OLD, SimulatedOperator

from scripts.static_ops import app_release as app
from scripts.static_ops import publication_gate as gate
from scripts.static_ops.release_automation import ActionsOperator

TOOL = "d" * 40
SCHEMA = "data/schemas/project.schema.json"
WRITER = "web/src/localProjectRepository.ts"
ROLES = ("old_to_new", "old_writer_fenced", "storage_sync_roundtrip")


def blob(source):
    return hashlib.sha1(source.encode(), usedforsecurity=False).hexdigest()


@pytest.fixture
def reviewed(tmp_path):
    before = {"$defs": {"RouteNode": {"properties": {"name": {"type": "string"}}}}}
    after = copy.deepcopy(before)
    after["$defs"].update(ReferencePointSnapshot={}, ReferencePointSource={})
    after["$defs"]["RouteNode"]["properties"]["reference_snapshot"] = {
        "anyOf": [{"$ref": "#/$defs/ReferencePointSnapshot"}, {"type": "null"}],
        "default": None,
    }
    sources = {
        (OLD, SCHEMA): json.dumps(before),
        (NEW, SCHEMA): json.dumps(after),
        (OLD, WRITER): "factory.open(name, 1)",
        (
            NEW,
            WRITER,
        ): "export const LOCAL_DATABASE_VERSION = 2; factory.open(name, LOCAL_DATABASE_VERSION)",
        (OLD, app.COMPATIBILITY[0]): "rules",
        (NEW, app.COMPATIBILITY[0]): "rules",
        (OLD, app.COMPATIBILITY[1]): "indexes",
        (NEW, app.COMPATIBILITY[1]): "indexes",
    }
    path = tmp_path / "schema-transition.json"
    operator = app.Operator(tmp_path, schema_transition=path)

    def git(*args):
        if args == ("rev-parse", "HEAD"):
            return TOOL
        sha, name = args[-1].split(":", 1)
        return blob(sources[sha, name]) if args[0] == "rev-parse" else sources[sha, name]

    operator.git = git
    operator.rules_source = lambda sha: sources[sha, app.COMPATIBILITY[0]]
    variables = {name: "synthetic" for name in gate.CONFIGURATION}
    variables["VITE_FIREBASE_PROJECT_ID"] = "test-project"
    record = {
        "schema_version": 1,
        "kind": "reference-snapshot-writer-fence",
        "sha": NEW,
        "current_sha": OLD,
        "tooling_sha": TOOL,
        "project": "test-project",
        "origin": gate.ORIGIN,
        "configuration_digest": app.fingerprint(app.public_configuration(variables)),
        "direction": "old-to-new",
        "recovery": "forward-fix; preserve Project data",
        "database_version": 2,
        "before_blob": blob(sources[OLD, SCHEMA]),
        "after_blob": blob(sources[NEW, SCHEMA]),
        "writer_blob": blob(sources[NEW, WRITER]),
        "checks": {},
    }
    for role in ROLES:
        evidence = tmp_path / (role + ".txt")
        evidence.write_text("Synthetic reviewed result: " + role)
        record["checks"][role] = {
            "result": "PASS",
            "reviewed_by": "synthetic-reviewer",
            "summary": role,
            "record": {"path": evidence.name, "sha256": app.digest(evidence.read_bytes())},
        }
    app.write_json(path, record)
    calls = []

    def rules_get(resource):
        calls.append(resource)
        if "/releases/" in resource:
            return {"rulesetName": "projects/test-project/rulesets/current"}
        return {"source": {"files": [{"content": "rules"}]}}

    operator.rules_get = rules_get
    return operator, path, record, sources, variables, calls


def test_exact_reviewed_transition_reads_rules_but_does_not_write_them(reviewed):
    operator, path, record, _, variables, calls = reviewed
    digest = operator.compatibility(NEW, OLD, variables)
    assert len(digest) == 64 and len(calls) == 3
    assert operator.schema_receipt["record"] == record
    assert operator.schema_receipt["sha256"] == app.digest(path.read_bytes())
    assert set(operator.schema_receipt["evidence"]) == set(ROLES)
    assert operator.rules_receipt["transition"] is None


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", True),
        ("kind", "generic"),
        ("sha", OLD),
        ("current_sha", NEW),
        ("tooling_sha", OLD),
        ("project", "other"),
        ("origin", "https://other.example"),
        ("configuration_digest", "unknown"),
        ("direction", "bidirectional"),
        ("recovery", "rollback"),
        ("database_version", True),
        ("database_version", 1),
        ("before_blob", "wrong"),
        ("after_blob", "wrong"),
        ("writer_blob", "wrong"),
        ("checks", {}),
    ],
)
def test_mismatched_or_unverified_record_blocks_before_provider_reads(reviewed, key, value):
    operator, path, record, _, variables, calls = reviewed
    record[key] = value
    app.write_json(path, record)
    with pytest.raises(app.Blocked):
        operator.compatibility(NEW, OLD, variables)
    assert calls == [] and operator.rules_receipt is None


@pytest.mark.parametrize(
    "damage", ["missing", "invalid", "withdrawn", "reviewer", "evidence", "hash", "path"]
)
def test_missing_withdrawn_or_replaced_evidence_is_not_acceptance(reviewed, damage):
    operator, path, record, _, variables, calls = reviewed
    if damage == "missing":
        path.unlink()
    elif damage == "invalid":
        path.write_text("[]")
    else:
        check = record["checks"]["old_writer_fenced"]
        if damage == "withdrawn":
            check["result"] = "UNKNOWN"
        elif damage == "reviewer":
            check["reviewed_by"] = " "
        elif damage == "evidence":
            (path.parent / check["record"]["path"]).write_text("replaced")
        elif damage == "hash":
            check["record"]["sha256"] = "f" * 64
        else:
            check["record"]["path"] = "missing-file"
        app.write_json(path, record)
    with pytest.raises(app.Blocked):
        operator.compatibility(NEW, OLD, variables)
    assert calls == []


@pytest.mark.parametrize(
    "change", ["required", "type", "indexes", "writer", "writer-use", "previous-writer"]
)
def test_record_cannot_authorize_broader_schema_or_unfenced_candidate(reviewed, change):
    operator, path, record, sources, variables, _ = reviewed
    if change in {"required", "type"}:
        schema = json.loads(sources[NEW, SCHEMA])
        if change == "required":
            schema["required"] = ["new_required_field"]
        else:
            schema["$defs"]["RouteNode"]["properties"]["name"]["type"] = "integer"
        sources[NEW, SCHEMA] = json.dumps(schema)
        record["after_blob"] = blob(sources[NEW, SCHEMA])
    elif change == "indexes":
        sources[NEW, app.COMPATIBILITY[1]] = "changed index"
    else:
        sources[NEW if change != "previous-writer" else OLD, WRITER] = (
            sources[OLD, WRITER]
            if change == "writer"
            else "export const LOCAL_DATABASE_VERSION = 2;"
        )
        record["writer_blob"] = blob(sources[NEW, WRITER])
    app.write_json(path, record)
    with pytest.raises(app.Blocked):
        operator.compatibility(NEW, OLD, variables)


def test_normal_and_actions_operator_still_reject_changed_schema(reviewed):
    operator, _, _, _, variables, calls = reviewed
    for target in (app.Operator(operator.root), ActionsOperator({"id": 1})):
        target.git, target.rules_source, target.rules_get = (
            operator.git,
            operator.rules_source,
            operator.rules_get,
        )
        assert target.schema_transition is None
        with pytest.raises(app.Blocked, match="migration required"):
            target.compatibility(NEW, OLD, variables)
    assert calls == []


@pytest.mark.parametrize("change", ["record", "evidence", "source", "path"])
def test_prepare_publish_binds_exact_record_evidence_and_candidate(reviewed, change):
    real, path, record, sources, variables, _ = reviewed
    operator = SimulatedOperator(real.root, automation="false")
    operator.state["variables"].update(variables)

    def refresh(simulated, paused):
        simulated.state["rules_digest"] = real.compatibility(NEW, OLD, variables)
        simulated.state["schema_receipt"] = copy.deepcopy(real.schema_receipt)

    operator.snapshot_hook = refresh
    store = real.root / "store"
    store.mkdir(mode=0o700)
    app.prepare(
        operator, NEW, OLD, store, app.Journal(store / "prepare-status.json", "prepare", NEW)
    )
    plan = json.loads((store / "plan.json").read_text())
    assert plan["snapshot"]["schema_receipt"]["record"] == record
    if change == "record":
        record["checks"]["old_to_new"]["summary"] = "different reviewed result"
        app.write_json(path, record)
    elif change == "evidence":
        reference = record["checks"]["old_to_new"]["record"]
        (path.parent / reference["path"]).write_text("different result")
    elif change == "source":
        sources[NEW, WRITER] += " changed"
    else:
        other = path.with_name("replacement.json")
        other.write_bytes(path.read_bytes())
        real.schema_transition = other
    with pytest.raises(app.Blocked):
        app.publish(
            operator,
            NEW,
            OLD,
            store,
            app.digest((store / "plan.json").read_bytes()),
            app.Journal(store / "publish-status.json", "publish", NEW),
        )
    assert operator.uploaded is None


def test_rules_transition_is_reused_alongside_schema_evidence(reviewed):
    from test_static_app_release import transition_record

    operator, path, _, sources, variables, _ = reviewed
    sources[OLD, app.COMPATIBILITY[0]] = "old rules"
    operator.rules_transition = path.with_name("rules-transition.json")
    transition_record(
        operator.rules_transition, project="test-project", before="old rules", after="rules"
    )
    original = operator.rules_get

    def named(resource):
        value = original(resource)
        if "/rulesets/" in resource:
            value["source"]["files"][0]["name"] = "firebase/firestore.rules"
        return value

    operator.rules_get = named
    assert len(operator.compatibility(NEW, OLD, variables)) == 64
    assert operator.rules_receipt["transition"]["record"]["sha"] == NEW
    assert operator.schema_receipt["record"]["sha"] == NEW
