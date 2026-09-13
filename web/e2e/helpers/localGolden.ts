// Compare semantic outputs; random identities, timestamps and cache fingerprints are not Golden values.
export function localGolden(state: any) {
  const sectionIds = state.project.sections.map((section: any) => section.id);
  const nodeIds = state.project.route_nodes.map((node: any) => node.id);
  const sectionIndex = (id: string | null) => id === null ? null : sectionIds.indexOf(id);
  const cell = (value: any) => ({
    text: value.text, state: value.state, reason_code: value.reason_code, manual: value.manual,
  });
  const issues = (values: any[]) => values.map((issue: any) => ({
    code: issue.code, severity: issue.severity,
    section: sectionIndex(issue.section_id ?? issue.sectionId ?? null),
    segment: issue.segment_sequence ?? issue.segmentSequence ?? null,
  }));
  return {
    physicalLegs: state.project.sections.map((section: any) => ({
      from: nodeIds.indexOf(section.from_node_id), to: nodeIds.indexOf(section.to_node_id),
      phase: section.phase, altitude: section.planned_altitude_ft_msl,
    })),
    nodes: state.project.route_nodes.map((node: any) => ({
      name: node.name, latitude: node.latitude_deg, longitude: node.longitude_deg, role: node.role,
    })),
    zones: state.outcome.sections.map((zone: any) => Object.fromEntries(
      Object.entries(zone).filter(([key]) => !key.endsWith("_id") && key !== "performance_metadata")
        .map(([key, value]: [string, any]) => [key, value && typeof value === "object" && "adopted_source" in value
          ? { value: value.adopted_source === "MANUAL" ? value.manual_override?.value : value.automatic_value,
              status: value.automatic_status, source: value.adopted_source, warnings: value.warnings }
          : value]),
    )),
    boundaries: state.outcome.derived_points.map(({section_id, ...point}: any) => ({
      ...point, section: sectionIndex(section_id),
    })),
    display: state.outcome.display_rows.map((row: any) => Object.fromEntries(
      Object.entries(row).filter(([key, value]) =>
        ["phase", "row_type", "from_name", "to_name", "pa_display_kind"].includes(key) ||
        (value && typeof value === "object" && "text" in value))
        .map(([key, value]: [string, any]) => [key, value && typeof value === "object" ? cell(value) : value]),
    )),
    fuel: state.outcome.fuel_plan,
    summary: state.outcome.summary,
    issues: issues(state.outcome.issues),
    readiness: issues(state.readiness.issues),
    status: state.outcome.status,
    converged: state.outcome.converged,
    policy: state.outcome.policy_version,
    performance: state.outcome.performance_table_version,
  };
}

// Keep the #117 Golden unchanged; #125 additionally checks provenance and ownership.
export function calculationCoreGolden(state: any) {
  const ids = new Map<string, string>();
  state.project.route_nodes.forEach((node: any, index: number) => ids.set(node.id, `node:${index}`));
  state.project.sections.forEach((section: any, index: number) => ids.set(section.id, `section:${index}`));
  ids.set(state.project.id, "project");
  state.project.visual_references.forEach((point: any, index: number) => ids.set(point.id, `reference:${index}`));
  function semantic(value: any, key = ""): any {
    if (typeof value === "string") {
      if (ids.has(value)) return ids.get(value);
      // Compound request identities retain phase/sequence while replacing random owner UUIDs.
      for (const [id, label] of ids) value = value.replaceAll(id, label);
      return value;
    }
    if (Array.isArray(value)) return value.map(item => semantic(item));
    if (value && typeof value === "object" && ["fuel", "ete", "distance"].includes(key) &&
        typeof value.effective_value === "string" && /^-?[0-9.e+-]+\/-?[0-9.e+-]+$/.test(value.effective_value)) {
      // These three cells encode *internal floats* as a pair; text is the display contract.
      value = { ...value, effective_value: value.effective_value.split("/").map(Number) };
    }
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
      .map(([name, item]) => [semantic(name), name === "generated_against_fingerprint"
        ? "<input-identity fingerprint>" : semantic(item, name)]));
    return value;
  }
  return {
    ...localGolden(state),
    canonical: semantic(state.outcome),
  };
}
