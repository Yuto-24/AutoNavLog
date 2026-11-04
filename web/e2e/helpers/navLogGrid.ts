import { expect, type Page } from "@playwright/test";

/** Issue #276: assert the paper column groups independently of the CSS selectors. */
export async function expectNavLogPaperGrid(page: Page) {
  const grid = await page.locator(".nav-log-table").evaluate((table) => {
    const headers = Array.from(table.querySelectorAll("thead th"));
    const cellStyle = (cell: Element) => {
      const style = getComputedStyle(cell);
      const box = cell.getBoundingClientRect();
      return {
        right: style.borderRightWidth,
        rightStyle: style.borderRightStyle,
        bottom: style.borderBottomWidth,
        bottomStyle: style.borderBottomStyle,
        x: box.x,
        width: box.width,
      };
    };
    const style = getComputedStyle(table);
    return {
      top: style.borderTopWidth,
      left: style.borderLeftWidth,
      labels: headers.map((cell) => cell.textContent?.replace(/\s+/g, " ").trim()),
      headers: headers.map(cellStyle),
      rows: Array.from(table.querySelectorAll("tbody tr")).map((row) => ({
        type: row.getAttribute("data-row-type"),
        cells: Array.from(row.children).map(cellStyle),
      })),
    };
  });
  expect(grid.top).toBe("2px");
  expect(grid.left).toBe("2px");
  const groupEnds = new Set(["TO", "TASkt", "MC", "MH", "ZONE / CUMETE min", "ETO", "ATE"]);
  // BR separates rendered header lines but is not a textContent character.
  const strong = grid.labels.map((label, index) => groupEnds.has(label ?? "") || index === grid.labels.length - 1);
  expect(strong.filter(Boolean)).toHaveLength(8);
  for (const [index, header] of grid.headers.entries()) {
    expect(header.right).toBe(strong[index] ? "2px" : "1px");
    expect(header.rightStyle).toBe("solid");
    expect(header.bottom).toBe("2px");
    expect(header.bottomStyle).toBe("solid");
  }
  expect(grid.rows.some((row) => row.type === "LEG_SEPARATOR")).toBe(true);
  expect(grid.rows.some((row) => row.type === "PHYSICAL_LEG_SUMMARY")).toBe(true);
  expect(grid.rows.some((row) => row.type === "CALCULATION_ZONE")).toBe(true);
  expect(grid.rows.some((row) => row.type === "DESTINATION_INFO")).toBe(true);
  for (const [rowIndex, row] of grid.rows.entries()) {
    expect(row.cells).toHaveLength(grid.headers.length);
    for (const [index, cell] of row.cells.entries()) {
      expect(cell.right).toBe(strong[index] ? "2px" : "1px");
      expect(cell.rightStyle).toBe("solid");
      const last = rowIndex === grid.rows.length - 1;
      expect(cell.bottom).toBe(last ? "2px" : "1px");
      expect(cell.bottomStyle).toBe(last ? "solid" : "dotted");
      expect(Math.abs(cell.x - grid.headers[index].x)).toBeLessThanOrEqual(1);
      expect(Math.abs(cell.width - grid.headers[index].width)).toBeLessThanOrEqual(1);
    }
  }
}
