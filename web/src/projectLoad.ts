import type { AirportOption, Project } from "./types";
import { emptyCheckPointDraft, projectDraft, type ApplicationSession } from "./applicationSession";
import { formFromProject } from "./forms";
import { draftFromSection } from "./navLogEditing";

// Project metadata objects can arrive with different key order after validation.
export function sameProjectContent(left: unknown, right: unknown): boolean {
  const normalize = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(normalize);
    if (value && typeof value === "object") return Object.fromEntries(
      Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, normalize(item)]),
    );
    return value;
  };
  return JSON.stringify(normalize(left)) === JSON.stringify(normalize(right));
}

export function sameProjectDraft(left: Project, right: Project): boolean {
  // Readiness and timestamps are derived, not user edits.
  const content = ({ updated_at: _updated, status: _status, ...project }: Project) => project;
  return sameProjectContent(content(left), content(right));
}

export function hasProjectInputChanges(session: ApplicationSession, project: Project, airports: AirportOption[]): boolean {
  const point = project.visual_references.find(item => item.id === session.checkPointDraft.id);
  const checkPoint = point ? { id: point.id, name: point.name, latitude: String(point.latitude_deg),
    longitude: String(point.longitude_deg), linkedSectionId: point.linked_section_id ?? "" } : emptyCheckPointDraft();
  return !sameProjectContent(session.form, formFromProject(project, session.form, airports))
    || !sameProjectContent(session.projectDraft, projectDraft(project))
    || project.sections.some(section =>
      (session.altitudeInputs[section.id] !== undefined && session.altitudeInputs[section.id] !== String(section.planned_altitude_ft_msl))
      || (session.navLogDrafts[section.id] !== undefined && !sameProjectContent(session.navLogDrafts[section.id], draftFromSection(section))))
    || session.projectName !== project.name
    || !sameProjectContent(session.checkPointDraft, checkPoint)
    || Boolean(session.nodeNameDraft.id && session.nodeNameDraft.name !== project.route_nodes.find(node => node.id === session.nodeNameDraft.id)?.name);
}
