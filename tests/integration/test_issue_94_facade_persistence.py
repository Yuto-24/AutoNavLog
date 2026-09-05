from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonavlog.domain.calculation import Issue, RjfmInboundGuidance
from autonavlog.domain.enums import IssueSeverity
from autonavlog.importers.kml import import_kml_text
from autonavlog.web.facade import AutoNavLogWebApplication, WebApplicationError
from autonavlog.web.models import ConfirmRouteRequest, SaveProjectRequest, UpdateProjectRequest
from autonavlog.web.runtime import WebRuntimeConfig, build_web_application

ROOT = Path(__file__).resolve().parents[2]
OWNER = "pilot@example.com"

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark>
<name>RJFM-RJFO</name><LineString><coordinates>
131.4486111111,31.8772222222,0
131.5000000000,32.4000000000,0
131.6500000000,33.1000000000,0
131.7000000000,33.4000000000,0
131.7372222222,33.4794444444,0
</coordinates></LineString></Placemark></Document></kml>
"""


def _application(storage_root: Path) -> AutoNavLogWebApplication:
    return build_web_application(
        WebRuntimeConfig(
            data_root=ROOT / "data",
            storage_root=storage_root,
            weather_mode="fake",
            trusted_local_identity=OWNER,
        )
    )


def _new_project(
    application: AutoNavLogWebApplication,
    *,
    owner: str = OWNER,
    weather_mode: str = "FTD",
) -> tuple[object, dict[str, object]]:
    session = application.create_session(owner)
    application.accept_import(
        session,
        result=import_kml_text(KML),
        filename="route.kml",
    )
    payload = {
        "candidate_kind": "line",
        "candidate_index": 0,
        "route_use_confirmed": True,
        "flight_date": "2026-08-10" if weather_mode == "FORECAST" else "2099-08-10",
        "departure_time_jst": "09:00",
        "total_usable_fuel_gal": 90,
        "default_variation_deg_east": 8,
        "weather_mode": weather_mode,
        "all_leg_altitude_ft_msl": 3500,
        "defaults_confirmed": True,
    }
    if weather_mode == "FTD":
        payload["ftd_weather"] = {
            "surface_wind": {"direction_deg_from": 180, "speed_kt": 5},
            "wind_at_5000_ft": {"direction_deg_from": 270, "speed_kt": 20},
        }
    state = application.confirm_route(
        session,
        ConfirmRouteRequest.model_validate(payload),
    )
    return session, state


def _update_request(project: object, *, fuel_gal: float) -> UpdateProjectRequest:
    assert isinstance(project, dict)
    ui_state = project["metadata"]["ui_state"]
    arrival = ui_state["arrival_plan"]
    return UpdateProjectRequest.model_validate(
        {
            "flight_date": project["flight_date"],
            "departure_time_jst": "09:00",
            "pilot_name": project["pilot_name"],
            "ship_identifier": project["ship_identifier"],
            "total_usable_fuel_gal": fuel_gal,
            "default_variation_deg_east": project["default_variation_deg_east"],
            "weather_mode": project["weather_mode"],
            "ftd_weather": project["ftd_weather"],
            "run_up_included": project["run_up_included"],
            "nose_fairing_enabled": project["nose_fairing_enabled"],
            "air_conditioning_enabled": project["air_conditioning_enabled"],
            "descent_rate_fpm": project["descent_rate_fpm"],
            "tgl_count": project["tgl_count"],
            "sections": [
                {
                    "section_id": section["id"],
                    "planned_altitude_ft_msl": section["planned_altitude_ft_msl"],
                    "phase": section["phase"],
                    "manual_wind_direction_deg": section["manual_wind_direction_deg"],
                    "manual_wind_speed_kt": section["manual_wind_speed_kt"],
                    "manual_wind_by_phase": section["manual_wind_by_phase"],
                    "manual_temperature_c": section["manual_temperature_c"],
                    "manual_temperature_c_by_phase": section[
                        "manual_temperature_c_by_phase"
                    ],
                    "manual_tas_kt": section["manual_tas_kt"],
                }
                for section in project["sections"]
            ],
            "visual_reporting_point_node_id": arrival[
                "visual_reporting_point_node_id"
            ],
            "selected_pattern_altitude_ft_msl": arrival[
                "selected_pattern_altitude_ft_msl"
            ],
            "arrival_altitude_mode": arrival["altitude_mode"],
            "manual_vrep_altitude_ft_msl": arrival[
                "manual_vrep_altitude_ft_msl"
            ],
            "manual_vrep_reason": arrival["manual_override_reason"],
        }
    )


def test_last_good_restores_across_sessions_and_application_instances(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    session, confirmed = _new_project(application)

    calculated = application.calculate(session)

    project_id = calculated["project"]["id"]
    assert calculated["outcome"]["project_id"] == project_id
    assert calculated["destinationWind"]["reason_code"] == "FTD_MODE_NO_TAF"
    restored = application.create_session(OWNER)
    restored_state = application.present(restored)
    assert restored_state["project"]["id"] == project_id
    assert restored_state["outcome"]["project_id"] == project_id
    assert restored_state["readiness"]["calculationIsCurrent"] is True

    restarted = _application(storage_root)
    restarted_state = restarted.present(restarted.create_session(OWNER))
    assert restarted_state["project"]["id"] == project_id
    assert restarted_state["outcome"]["project_id"] == project_id
    assert restarted_state["destinationWind"]["reason_code"] == "FTD_MODE_NO_TAF"
    assert confirmed["project"]["revision"] == 0


def test_last_opened_is_owner_isolated_and_not_updated_at_maximum(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path / "storage")
    first_session, first_state = _new_project(application)
    first_id = first_state["project"]["id"]
    second_session, second_state = _new_project(application)
    second_id = second_state["project"]["id"]
    assert first_id != second_id

    application.load(first_session, first_session.project.id)
    assert second_session.project is not None
    future_second = second_session.project.model_copy(
        update={"updated_at": datetime(2100, 1, 1, tzinfo=UTC)}
    )
    application.project_service.autosave(future_second)

    restored = application.create_session(OWNER)
    assert restored.project is not None
    assert str(restored.project.id) == first_id
    other = application.create_session("other@example.com")
    assert other.project is None
    assert application.present(other)["savedProjects"] == []

    application.invalidate_session(
        restored.token,
        OWNER,
        clear_last_opened=True,
    )
    assert application.create_session(OWNER).project is None


def test_failed_and_blocked_calculations_preserve_last_good(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application(tmp_path / "storage")
    session, _ = _new_project(application)
    good = application.calculate(session)
    assert session.project is not None
    project_id = session.project.id
    owner_key = application.project_service.owner_key(OWNER)
    original = application.project_service.repository.load_last_calculation(
        project_id,
        owner_key=owner_key,
    )
    assert original is not None

    assert session.outcome is not None
    blocked_outcome = session.outcome.model_copy(
        update={
            "issues": [
                *session.outcome.issues,
                Issue(
                    code="TEST_BLOCKER",
                    severity=IssueSeverity.BLOCKER,
                    message="blocked",
                ),
            ]
        }
    )
    blocked = session.readiness_service.record_calculation(
        session.project,
        blocked_outcome,
    )
    application._persist_calculation(session, blocked, session.destination_wind)
    after_blocked = application.project_service.repository.load_last_calculation(
        project_id,
        owner_key=owner_key,
    )
    assert after_blocked == original

    request = _update_request(good["project"], fuel_gal=75)

    def fail(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("injected calculation failure")

    monkeypatch.setattr(application, "_calculate_outcome", fail)
    with pytest.raises(RuntimeError, match="injected"):
        application.update_and_calculate(session, request)
    assert session.project.total_usable_fuel_gal == 75
    assert session.readiness is not None
    assert session.readiness.calculation_is_current is False
    after_failure = application.project_service.repository.load_last_calculation(
        project_id,
        owner_key=owner_key,
    )
    assert after_failure == original
    assert application.project_service.load(project_id).total_usable_fuel_gal == 75


def test_forecast_identity_metadata_and_warning_outcome_are_persisted(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    application.development_weather = False
    session, _ = _new_project(application, weather_mode="FORECAST")

    calculated = application.calculate(session)

    project_id = session.project.id
    owner_key = application.project_service.owner_key(OWNER)
    record = application.project_service.repository.load_last_calculation(
        project_id,
        owner_key=owner_key,
    )
    assert record is not None
    forecast_run_id = calculated["outcome"]["selected_forecast_run_id"]
    assert isinstance(forecast_run_id, str)
    assert len(forecast_run_id) == 14
    assert record.forecast_metadata["provider"] == "fake"
    assert record.forecast_metadata["forecast_run_id"] == forecast_run_id

    restarted = _application(storage_root)
    restarted.development_weather = False
    restored_state = restarted.present(restarted.create_session(OWNER))
    assert restored_state["project"]["selected_forecast_run_id"] == forecast_run_id
    assert restored_state["outcome"]["selected_forecast_run_id"] == forecast_run_id

    assert session.project is not None
    assert session.outcome is not None
    warning_outcome = session.outcome.model_copy(
        update={
            "issues": [
                *session.outcome.issues,
                Issue(
                    code="TEST_WARNING",
                    severity=IssueSeverity.WARNING,
                    message="warning",
                ),
            ]
        }
    )
    warning_materialized = session.readiness_service.record_calculation(
        session.project,
        warning_outcome,
    )
    application._persist_calculation(session, warning_materialized, session.destination_wind)

    updated_record = application.project_service.repository.load_last_calculation(
        project_id,
        owner_key=owner_key,
    )
    assert updated_record is not None
    assert any(issue.code == "TEST_WARNING" for issue in updated_record.outcome.issues)


def test_inbound_presentation_keeps_warnings_and_redacts_unsafe_available_values(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path / "storage")
    session, _ = _new_project(application)
    application.calculate(session)
    assert session.outcome is not None

    unavailable = RjfmInboundGuidance(
        status="UNAVAILABLE",
        reason_code="REFERENCE_LOAD_FAILED",
        message="reference unavailable",
        generated_against_fingerprint="a" * 64,
    )
    session.outcome = session.outcome.model_copy(
        update={"rjfm_inbound_guidance": unavailable}
    )
    warning_state = application.present(session)
    assert warning_state["outcome"]["rjfm_inbound_guidance"] == unavailable.model_dump(
        mode="json"
    )

    unsafe_available = RjfmInboundGuidance(
        status="AVAILABLE",
        message="unsafe stale numerics",
        generated_against_fingerprint="b" * 64,
        reference_revision="wrong-reference",
        reference_content_fingerprint="c" * 64,
        rounded_dme_nm=12.5,
        rounded_turn_altitude_ft_msl=3200,
    )
    session.outcome = session.outcome.model_copy(
        update={"rjfm_inbound_guidance": unsafe_available}
    )
    redacted_state = application.present(session)
    redacted = redacted_state["outcome"]["rjfm_inbound_guidance"]
    assert redacted["status"] == "AVAILABLE"
    assert redacted["message"] == "unsafe stale numerics"
    assert redacted["rounded_dme_nm"] is None
    assert redacted["rounded_turn_altitude_ft_msl"] is None


def test_explicit_save_does_not_publish_checkpoint_when_draft_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    session, _ = _new_project(application)
    assert session.project is not None
    project_id = session.project.id

    def fail_autosave(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected autosave failure")

    monkeypatch.setattr(application.project_service, "autosave", fail_autosave)

    with pytest.raises(WebApplicationError) as error:
        application.save(session, SaveProjectRequest(name="should-not-checkpoint"))

    assert error.value.code == "PROJECT_PERSISTENCE_FAILED"
    assert not (storage_root / "projects" / str(project_id) / "project.json").exists()


def test_delete_failure_keeps_project_session_and_last_opened_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application(tmp_path / "storage")
    session, _ = _new_project(application)
    assert session.project is not None
    project_id = session.project.id
    repository = application.project_service.repository

    def fail_delete(_project_id: object) -> None:
        raise OSError("injected Project delete failure")

    monkeypatch.setattr(repository, "_delete_locked", fail_delete)

    with pytest.raises(WebApplicationError) as error:
        application.delete(session, project_id)

    assert error.value.code == "PROJECT_PERSISTENCE_FAILED"
    assert session.project is not None
    assert session.project.id == project_id
    assert application.project_service.last_opened_project(OWNER) == project_id
    assert application.project_service.load(project_id).id == project_id
