from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from autonavlog.domain.calculation import Issue, RjfmInboundGuidance
from autonavlog.domain.enums import IssueSeverity
from autonavlog.domain.weather import ForecastRequirement, ForecastRun, RunSelectionStatus
from autonavlog.importers.kml import import_kml_text
from autonavlog.weather.fake_provider import FakeWeatherProvider
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


class BoundedForecastWeatherProvider(FakeWeatherProvider):
    old_run_id = "20260809000000"
    fresh_run_id = "20260810000000"
    cutoff_utc = datetime(2026, 8, 10, 0, 30, tzinfo=UTC)

    def __init__(self) -> None:
        super().__init__(runs=(self.fresh_run_id, self.old_run_id))

    @classmethod
    def _selected_run(cls, requirement: ForecastRequirement) -> str:
        return (
            cls.fresh_run_id
            if min(requirement.valid_times_utc) >= cls.cutoff_utc
            else cls.old_run_id
        )

    def resolve_run(self, requirement: ForecastRequirement) -> ForecastRun:
        run_id = self._selected_run(requirement)
        return ForecastRun(id=run_id, initial_time_utc=self._run_datetime(run_id))

    def inspect_run_status(
        self,
        selected_run_id: str,
        requirement: ForecastRequirement,
    ) -> RunSelectionStatus:
        latest_compatible_run_id = self._selected_run(requirement)
        return RunSelectionStatus(
            selected_run_id=selected_run_id,
            latest_compatible_run_id=latest_compatible_run_id,
            selected_run_covers_requirement=(selected_run_id == latest_compatible_run_id),
        )


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
    application.save(first_session, SaveProjectRequest(name="first"))
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


def test_owner_list_keeps_only_latest_draft_and_named_checkpoints(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    first_session, first_state = _new_project(application)
    first_id = first_state["project"]["id"]
    first_summaries = application.present(first_session)["savedProjects"]
    assert [(item["id"], item["kind"]) for item in first_summaries] == [
        (first_id, "LATEST")
    ]

    named = application.save(first_session, SaveProjectRequest(name="checkpoint"))
    assert [
        (item["name"], item["kind"], item["revision"])
        for item in named["savedProjects"]
    ] == [
        ("checkpoint", "SAVED", 1)
    ]

    second_session, second_state = _new_project(application)
    second_id = second_state["project"]["id"]
    assert second_id != first_id
    summaries = application.present(second_session)["savedProjects"]
    assert {(item["id"], item["name"], item["kind"]) for item in summaries} == {
        (first_id, "checkpoint", "SAVED"),
        (second_id, second_state["project"]["name"], "LATEST"),
    }

    application.invalidate_session(
        second_session.token,
        OWNER,
        clear_last_opened=True,
    )
    blank = application.create_session(OWNER)
    blank_state = application.present(blank)
    assert blank_state["project"] is None
    assert {item["kind"] for item in blank_state["savedProjects"]} == {"SAVED", "LATEST"}

    third_session, third_state = _new_project(application)
    third_id = third_state["project"]["id"]
    assert third_id not in {first_id, second_id}
    assert not (storage_root / "projects" / second_id).exists()
    final_summaries = application.present(third_session)["savedProjects"]
    assert {item["id"] for item in final_summaries} == {first_id, third_id}


def test_stale_calculation_cannot_replace_newer_session_latest(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    stale_session, stale_state = _new_project(application)
    stale_id = stale_state["project"]["id"]
    active_session, active_state = _new_project(application)
    active_id = active_state["project"]["id"]

    # The active route confirmation has already retired the first draft.  A
    # calculation finishing from the old session must not promote it again.
    application.calculate(stale_session)

    active = application.present(active_session)
    assert active["project"]["id"] == active_id
    assert [(item["id"], item["kind"]) for item in active["savedProjects"]] == [
        (active_id, "LATEST")
    ]
    assert (storage_root / "projects" / active_id).is_dir()
    assert stale_id != active_id


def test_editing_explicitly_loaded_project_retires_existing_latest(tmp_path: Path) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    saved_session, _ = _new_project(application)
    saved = application.save(saved_session, SaveProjectRequest(name="saved"))
    saved_id = saved["project"]["id"]
    latest_session, latest_state = _new_project(application)
    latest_id = latest_state["project"]["id"]

    loaded = application.load(saved_session, saved_session.project.id)
    application.update_project(
        saved_session,
        _update_request(loaded["project"], fuel_gal=91),
    )

    assert not (storage_root / "projects" / latest_id).exists()
    visible = application.present(latest_session)["savedProjects"]
    assert [(item["id"], item["kind"]) for item in visible] == [
        (saved_id, "SAVED")
    ]


def test_route_confirm_commits_latest_and_last_opened_in_one_owner_marker_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application(tmp_path / "storage")
    session, first = _new_project(application)
    assert session.project is not None
    first_id = session.project.id
    repository = application.project_service.repository
    owner_key = application.project_service.owner_key(OWNER)
    committed = repository.load_owner_state(owner_key)
    assert committed is not None
    assert committed.last_opened_project_id == first_id
    assert committed.latest_draft_project_id == first_id

    application.accept_import(
        session,
        result=import_kml_text(KML),
        filename="second-route.kml",
    )
    original_write = repository._write_owner_state_locked  # noqa: SLF001

    def fail_marker(*_args: object, **_kwargs: object) -> Path:
        raise OSError("injected owner marker failure")

    monkeypatch.setattr(repository, "_write_owner_state_locked", fail_marker)
    with pytest.raises(WebApplicationError, match="自動保存"):
        application.confirm_route(
            session,
            ConfirmRouteRequest.model_validate(
                {
                    "candidate_kind": "line",
                    "candidate_index": 0,
                    "route_use_confirmed": True,
                    "flight_date": "2099-08-10",
                    "departure_time_jst": "09:00",
                    "total_usable_fuel_gal": 90,
                    "default_variation_deg_east": 8,
                    "weather_mode": "FTD",
                    "ftd_weather": {
                        "surface_wind": {"direction_deg_from": 180, "speed_kt": 5},
                        "wind_at_5000_ft": {"direction_deg_from": 270, "speed_kt": 20},
                    },
                    "all_leg_altitude_ft_msl": 3500,
                    "defaults_confirmed": True,
                }
            ),
        )
    monkeypatch.setattr(repository, "_write_owner_state_locked", original_write)

    unchanged = repository.load_owner_state(owner_key)
    assert unchanged is not None
    assert unchanged.last_opened_project_id == first_id
    assert unchanged.latest_draft_project_id == first_id
    assert session.project is None
    assert first["project"]["id"] == str(first_id)


def test_route_confirm_recovers_from_corrupt_owner_marker_without_deleting_orphan(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    _, first = _new_project(application)
    first_id = first["project"]["id"]
    repository = application.project_service.repository
    owner_key = application.project_service.owner_key(OWNER)
    repository._owner_state_path(owner_key).write_text(  # noqa: SLF001 - corruption fixture
        '{"broken":',
        encoding="utf-8",
    )

    blank = application.create_session(OWNER)
    assert blank.project is None
    recovered_session, recovered = _new_project(application)
    recovered_id = recovered["project"]["id"]

    state = repository.load_owner_state(owner_key)
    assert state is not None
    assert str(state.last_opened_project_id) == recovered_id
    assert str(state.latest_draft_project_id) == recovered_id
    assert state.pending_cleanup_project_ids == []
    assert (storage_root / "projects" / first_id).is_dir()
    summaries = application.present(recovered_session)["savedProjects"]
    assert [(item["id"], item["kind"]) for item in summaries] == [
        (recovered_id, "LATEST")
    ]


def test_reset_and_explicit_delete_recover_from_corrupt_owner_marker(
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    session, _ = _new_project(application)
    saved = application.save(session, SaveProjectRequest(name="checkpoint"))
    project_id = saved["project"]["id"]
    repository = application.project_service.repository
    owner_key = application.project_service.owner_key(OWNER)
    marker_path = repository._owner_state_path(owner_key)  # noqa: SLF001 - corruption fixture
    marker_path.write_text('{"broken":', encoding="utf-8")

    # This is the same operation used by DELETE /api/session: no project
    # directory is inferred from corrupt state, and the marker is neutralized.
    application.clear_last_opened_project(OWNER)
    assert not marker_path.exists()
    assert marker_path.with_name("state.invalid.json").exists()

    blank = application.create_session(OWNER)
    marker_path.write_text('{"broken":', encoding="utf-8")
    assert session.project is not None
    deleted = application.delete(blank, session.project.id)

    assert deleted["savedProjects"] == []
    assert not (storage_root / "projects" / project_id).exists()


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


@pytest.mark.parametrize(
    ("flight_date", "departure_time_jst"),
    [
        (date(2026, 8, 11), "09:00"),
        (date(2026, 8, 10), "10:00"),
    ],
)
def test_saved_forecast_pin_is_invalidated_for_changed_departure_datetime(
    tmp_path: Path,
    flight_date: date,
    departure_time_jst: str,
) -> None:
    application = _application(tmp_path / "storage")
    application.development_weather = False
    session, state = _new_project(application, weather_mode="FORECAST")
    assert session.project is not None
    provider = BoundedForecastWeatherProvider()
    session.weather_provider = provider

    historical_run_id = provider.old_run_id
    saved_project = session.project.model_copy(
        update={"selected_forecast_run_id": historical_run_id}
    )
    application.project_service.autosave(saved_project, set_last_opened=True)
    loaded = application.load(session, saved_project.id)
    assert loaded["project"]["selected_forecast_run_id"] == historical_run_id

    request = _update_request(state["project"], fuel_gal=90).model_copy(
        update={
            "flight_date": flight_date,
            "departure_time_jst": departure_time_jst,
        }
    )
    updated = application.update_project(session, request)
    assert updated["project"]["selected_forecast_run_id"] is None

    calculated = application.calculate(session)
    assert calculated["outcome"]["selected_forecast_run_id"] == provider.fresh_run_id
    assert calculated["project"]["selected_forecast_run_id"] == provider.fresh_run_id
    prepared_requirement = provider.prepared[provider.fresh_run_id]
    assert provider.inspect_run_status(
        provider.fresh_run_id,
        prepared_requirement,
    ).selected_run_covers_requirement
    assert not any(item["severity"] == "BLOCKER" for item in calculated["outcome"]["issues"])


def test_saved_forecast_pin_is_retained_when_departure_datetime_is_unchanged(
    tmp_path: Path,
) -> None:
    application = _application(tmp_path / "storage")
    application.development_weather = False
    session, state = _new_project(application, weather_mode="FORECAST")
    assert session.project is not None
    provider = BoundedForecastWeatherProvider()
    session.weather_provider = provider

    saved_run_id = provider.old_run_id
    session.project = session.project.model_copy(
        update={"selected_forecast_run_id": saved_run_id}
    )
    updated = application.update_project(session, _update_request(state["project"], fuel_gal=75))

    assert updated["project"]["selected_forecast_run_id"] == saved_run_id


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


def test_explicit_save_survives_post_checkpoint_draft_refresh_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    application = _application(storage_root)
    session, _ = _new_project(application)
    original_autosave = application.project_service.autosave

    def fail_after_checkpoint(project: object, *, set_last_opened: bool = False) -> None:
        if getattr(project, "revision", 0) > 0:
            raise OSError("injected post-checkpoint autosave failure")
        original_autosave(project, set_last_opened=set_last_opened)

    monkeypatch.setattr(application.project_service, "autosave", fail_after_checkpoint)

    saved = application.save(session, SaveProjectRequest(name="checkpoint"))

    assert saved["project"]["name"] == "checkpoint"
    assert saved["project"]["revision"] == 1
    assert saved["savedProjects"] == [
        {
            "id": saved["project"]["id"],
            "name": "checkpoint",
            "status": saved["project"]["status"],
            "revision": 1,
            "updatedAt": saved["savedProjects"][0]["updatedAt"],
            "kind": "SAVED",
        }
    ]

    restarted = _application(storage_root)
    restored_state = restarted.present(restarted.create_session(OWNER))
    assert restored_state["project"]["name"] == "checkpoint"
    assert restored_state["project"]["revision"] == 1


def test_project_update_does_not_commit_session_when_draft_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application(tmp_path / "storage")
    session, state = _new_project(application)
    assert session.project is not None
    project_id = session.project.id
    original_session_project = session.project.model_copy(deep=True)
    original_stored_project = application.project_service.load(project_id)

    def fail_autosave(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected autosave failure")

    monkeypatch.setattr(application.project_service, "autosave", fail_autosave)

    with pytest.raises(WebApplicationError) as error:
        application.update_project(
            session,
            _update_request(state["project"], fuel_gal=75),
        )

    assert error.value.code == "PROJECT_PERSISTENCE_FAILED"
    assert session.project == original_session_project
    assert application.project_service.load(project_id) == original_stored_project


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
