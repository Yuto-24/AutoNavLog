from __future__ import annotations

from datetime import datetime, timedelta

from autonavlog.domain.project import Project
from autonavlog.domain.weather import ForecastRequirement
from autonavlog.nav.geodesy import geodesic_leg


class ForecastService:
    """Builds a conservative initial requirement without adopting weather values."""

    estimate_speed_kt = 100.0

    def build_initial_requirement(self, project: Project) -> ForecastRequirement:
        nodes = {node.id: node for node in project.route_nodes}
        elapsed_seconds = 0.0
        times = [project.planned_departure_time_jst]
        for section in project.ordered_sections():
            start = nodes[section.from_node_id]
            end = nodes[section.to_node_id]
            distance = (
                start.manual_distance_nm
                or geodesic_leg(
                    start.latitude_deg,
                    start.longitude_deg,
                    end.latitude_deg,
                    end.longitude_deg,
                ).distance_nm
            )
            speed = section.manual_tas_kt or self.estimate_speed_kt
            section_seconds = distance / speed * 3600.0
            times.append(
                project.planned_departure_time_jst
                + timedelta(seconds=elapsed_seconds + section_seconds / 2)
            )
            elapsed_seconds += section_seconds
        elapsed_seconds += 10 * 60 + project.tgl_count * 7 * 60
        times.append(project.planned_departure_time_jst + timedelta(seconds=elapsed_seconds))
        return ForecastRequirement(
            valid_times_utc=tuple(times),
            require_surface_temperature=True,
            require_estimated_qnh=False,
        )

    def build_final_requirement(
        self,
        project: Project,
        representative_times: tuple[datetime, ...],
        *,
        arrival_time_utc: datetime | None,
    ) -> ForecastRequirement:
        arrival_times = () if arrival_time_utc is None else (arrival_time_utc,)
        return ForecastRequirement(
            valid_times_utc=(
                project.planned_departure_time_jst,
                *representative_times,
                *arrival_times,
            ),
            require_surface_temperature=True,
            require_estimated_qnh=False,
        )
