from __future__ import annotations

import copy
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from autonavlog.domain.project import Project
from autonavlog.local_persistence import migrate_record


def record():
    project = Project(
        name="durable",
        flight_date="2026-09-15",
        planned_departure_time_jst="2026-09-15T09:00:00+09:00",
        departure_airport_id="RJFM",
        destination_airport_id="RJFT",
        total_usable_fuel_gal=90,
        default_variation_deg_east=-8,
    ).model_dump(mode="json")
    return {
        "schemaVersion": 2,
        "id": project["id"],
        "token": str(uuid4()),
        "checkpoint": None,
        "draft": project,
        "lastCalculation": None,
        "updatedAt": datetime.now(UTC).isoformat(),
    }


def test_old_schema_migration_is_pure_and_validated():
    original = record()
    original["schemaVersion"] = 1
    del original["updatedAt"]
    before = copy.deepcopy(original)
    migrated = migrate_record(original)
    assert migrated.schemaVersion == 2
    assert migrated.updatedAt == migrated.draft.updated_at
    assert original == before


@pytest.mark.parametrize(
    "damage", ["version", "identity", "checkpoint", "draft", "owner", "calculation"]
)
def test_migration_or_corruption_failure_never_modifies_original(damage):
    original = record()
    if damage == "version":
        original["schemaVersion"] = 999
    elif damage == "identity":
        original["id"] = str(uuid4())
    elif damage == "checkpoint":
        original["checkpoint"] = copy.deepcopy(original["draft"])
        original["checkpoint"]["revision"] += 1
    elif damage == "draft":
        original["draft"] = {"id": original["id"]}
    elif damage == "owner":
        original["draft"]["metadata"]["web_owner_id"] = "legacy"
    else:
        original["lastCalculation"] = {"calculation_fingerprint": "bad"}
    before = copy.deepcopy(original)
    with pytest.raises((ValidationError, ValueError)):
        migrate_record(original)
    assert original == before
    assert migrate_record(record()).schemaVersion == 2
