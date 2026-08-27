import { expect, test } from "@playwright/test";

import { formatOperationalMagneticCourse } from "../src/displayRounding";

test("operational MC boundary formatting matches the altitude-candidate course", () => {
  expect(formatOperationalMagneticCourse(179.499)).toBe("179");
  expect(formatOperationalMagneticCourse(179.5)).toBe("180");
  expect(formatOperationalMagneticCourse(180.499)).toBe("180");
  expect(formatOperationalMagneticCourse(359.499)).toBe("359");
  expect(formatOperationalMagneticCourse(359.5)).toBe("360");
  expect(formatOperationalMagneticCourse(0.499)).toBe("360");
  expect(formatOperationalMagneticCourse(0.5)).toBe("001");
});
