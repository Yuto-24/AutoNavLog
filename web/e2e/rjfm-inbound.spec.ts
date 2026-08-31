import { expect, test, type Page } from "@playwright/test";

const inboundKml = `<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
  <Placemark><name>RJFO→RJFM inbound</name><LineString><coordinates>
    131.7371201529664,33.47949406702627,0
    131.47036916946163,32.16255070087476,0
    131.42429852046251,31.985137767624444,0
    131.3535165268158,31.94977931375614,0
    131.4484517490447,31.87712141761355,0
  </coordinates></LineString></Placemark>
</Document></kml>`;

type State = {
  project: {
    route_nodes: Array<{
      id: string;
      sequence: number;
      name: string;
      latitude_deg: number;
      longitude_deg: number;
      role: string;
    }>;
    sections: Array<{
      id: string;
      sequence: number;
      from_node_id: string;
      to_node_id: string;
      phase: string;
      planned_altitude_ft_msl: number;
    }>;
    destination_airport_id: string;
  } | null;
  outcome: {
    sections: Array<{
      from_name: string;
      to_name: string;
      from_node_id: string | null;
      to_node_id: string | null;
      planned_altitude_ft_msl: { automatic_value?: number | null; adopted_source?: string | null };
    }>;
    display_rows: Array<{
      row_type: string;
      from_name: string;
      to_name: string;
      from_node_id: string | null;
      to_node_id: string | null;
      pa: { text: string | null };
    }>;
    derived_points: Array<{
      type: string;
      latitude_deg: number;
      longitude_deg: number;
    }>;
    rjfm_inbound_guidance?: {
      status: string;
      reason_code: string;
      message: string;
    } | null;
  } | null;
  altitudeGuidance: {
    sections: Array<{
      sectionId: string;
      inputMode: string;
      fixedAltitudeFtMsl: number | null;
    }>;
  };
};

async function readState(page: Page): Promise<State> {
  return await page.evaluate(async () => {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`state request failed: ${response.status}`);
    return await response.json() as State;
  });
}

test("real RJFO inbound KML keeps physical route and places EOC at UMK", async ({ page }, testInfo) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "経路を取り込む" })).toBeVisible();

  await page.getByRole("button", { name: "KMLを貼り付け" }).click();
  const dialog = page.getByRole("dialog", { name: "KML/XMLを貼り付け" });
  await dialog.getByRole("textbox").fill(inboundKml);
  await dialog.getByRole("button", { name: "貼付KMLを読み込む" }).click();
  await expect(page.getByLabel("飛行経路候補")).toHaveValue("line:0");
  await expect(page.getByLabel("FROM")).toHaveValue(/^RJFO\b/);
  await expect(page.getByLabel("TO")).toHaveValue(/^RJFM\b/);

  await page.getByLabel("気象モード").selectOption("FTD");
  await page.getByLabel("地上風向 ° FROM").fill("360");
  await page.getByLabel("地上風速 kt").fill("15");
  await page.getByLabel("5,000 ft風向 ° FROM").fill("270");
  await page.getByLabel("5,000 ft風速 kt").fill("30");
  await page.getByLabel("地図とKML記載順を確認しました").check();
  await page.getByRole("button", { name: "経路を確定" }).click();

  const routeRows = page.locator(".route-table tbody tr");
  await expect(routeRows).toHaveCount(5);
  const confirmed = await readState(page);
  if (!confirmed.project) throw new Error("confirmed project is missing");
  const physicalNodes = confirmed.project.route_nodes
    .slice()
    .sort((left, right) => left.sequence - right.sequence);
  expect(physicalNodes).toHaveLength(5);
  expect(physicalNodes.map((node) => node.role)).toEqual([
    "AIRPORT", "ROUTE_POINT", "ROUTE_POINT", "VISUAL_REPORTING_POINT", "DESTINATION",
  ]);
  expect(physicalNodes[0]?.name).toBe("RJFO");
  expect(physicalNodes[1]?.name).toBe("OMARU");
  expect(physicalNodes[2]?.name).toBe("UMK");
  expect(physicalNodes[4]?.name).toBe("RJFM");
  const physicalNodeIds = physicalNodes.map((node) => node.id);
  const inboundSection = confirmed.project.sections.find(
    (section) => section.from_node_id === physicalNodes[1]?.id
      && section.to_node_id === physicalNodes[2]?.id,
  );
  if (!inboundSection) throw new Error("OMARU→UMK section is missing");
  expect(inboundSection.planned_altitude_ft_msl).toBe(4500);
  const inboundGuidance = confirmed.altitudeGuidance.sections.find(
    (section) => section.sectionId === inboundSection.id,
  );
  expect(inboundGuidance).toMatchObject({
    inputMode: "RJFM_INBOUND_OMARU_TO_UMK_FIXED",
    fixedAltitudeFtMsl: 4500,
  });
  const fixedRouteControl = page.getByLabel("OMARU出発Legの固定高度");
  await expect(fixedRouteControl).toContainText("4,500 ft 固定");
  await expect(fixedRouteControl).toContainText("RJFM帰路 OMARU→UMKの到達高度");
  await expect(fixedRouteControl.locator("input, select")).toHaveCount(0);

  for (const input of await page.locator(".route-table tbody tr:not(.vrep-row) .table-number-input").all()) {
    await input.fill("4500");
  }
  for (const select of await page.locator(".altitude-candidate-select").all()) {
    await select.selectOption({ index: 0 });
  }
  const calculateButton = page.locator(".status-actions .primary-button");
  await expect(calculateButton).toHaveText("NAV LOGを作る");
  await calculateButton.click();
  await expect(page.getByRole("status", { name: "NAV LOGを計算中" })).toBeVisible();
  await expect(page.getByLabel("計算済みNAV LOG")).toBeFocused({ timeout: 30_000 });
  await expect(page.getByRole("status", { name: "NAV LOGを計算中" })).toBeHidden();

  const calculated = await readState(page);
  if (!calculated.project || !calculated.outcome) {
    throw new Error("calculated project/outcome is missing");
  }
  const calculatedNodes = calculated.project.route_nodes
    .slice()
    .sort((left, right) => left.sequence - right.sequence);
  expect(calculatedNodes.map((node) => node.id)).toEqual(physicalNodeIds);
  expect(calculatedNodes).toHaveLength(physicalNodes.length);
  expect(calculated.project.sections).toHaveLength(physicalNodes.length - 1);
  expect(calculated.project.sections.flatMap((section) => [section.from_node_id, section.to_node_id]))
    .toEqual(expect.arrayContaining(physicalNodeIds));

  const calculatedInboundSection = calculated.outcome.sections.find(
    (section) => section.from_node_id === physicalNodes[1]?.id
      && section.to_node_id === physicalNodes[2]?.id,
  );
  expect(calculatedInboundSection).toBeDefined();
  expect(calculatedInboundSection?.from_node_id).toBe(physicalNodes[1]?.id);
  expect(calculatedInboundSection?.to_node_id).toBe(physicalNodes[2]?.id);
  expect(calculatedInboundSection?.planned_altitude_ft_msl.automatic_value).toBe(4500);
  const navLogInbound = calculated.outcome.display_rows.find(
    (row) => row.row_type === "PHYSICAL_LEG_SUMMARY"
      && row.from_node_id === physicalNodes[1]?.id && row.to_node_id === physicalNodes[2]?.id,
  );
  expect(navLogInbound?.pa.text).toBe("4500");
  const eoc = calculated.outcome.derived_points.filter((point) => point.type === "EOC");
  expect(eoc).toHaveLength(1);
  expect(eoc[0]?.latitude_deg).toBeCloseTo(31.985137767624444, 5);
  expect(eoc[0]?.longitude_deg).toBeCloseTo(131.42429852046251, 5);
  expect(calculated.outcome.rjfm_inbound_guidance).toBeTruthy();
  await expect(page.getByLabel("RJFM帰路の経路延長案内")).toBeVisible();
  await expect(page.getByLabel("RJFM帰路の経路延長案内")).toContainText("warningのみ");
  const navLogBox = await page.getByLabel("計算済みNAV LOG").boundingBox();
  const guidanceBox = await page.getByLabel("RJFM帰路の経路延長案内").boundingBox();
  if (!navLogBox || !guidanceBox) throw new Error("NAV LOG or inbound guidance is missing");
  expect(guidanceBox.y).toBeGreaterThanOrEqual(navLogBox.y + navLogBox.height - 1);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: testInfo.outputPath("inbound-calculated-wide.png"), fullPage: true });
  await page.setViewportSize({ width: 1100, height: 1000 });
  await page.screenshot({ path: testInfo.outputPath("inbound-calculated-intermediate.png"), fullPage: true });
});


