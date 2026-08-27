import { expect, test } from "@playwright/test";

import { initialPlanningForm, touchAndGoCount } from "../src/forms";

test("TGL draft validates only whole numbers from 0 through 20", () => {
  const form = initialPlanningForm();
  const cases: Array<[string, number | null]> = [
    ["", null],
    ["-1", null],
    ["0", 0],
    ["3", 3],
    ["20", 20],
    ["21", null],
    ["1.5", null],
  ];

  for (const [draft, expected] of cases) {
    form.tglCount = draft;
    expect(touchAndGoCount(form)).toBe(expected);
  }
});
