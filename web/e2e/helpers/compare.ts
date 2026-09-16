export function compare(actual: any, expected: any, path = "golden"): void {
  if (typeof expected === "number") {
    if (typeof actual !== "number" || !Number.isFinite(actual) || Math.abs(actual - expected) > 1e-8)
      throw new Error(`${path}: expected ${expected}, received ${actual} (abs <= 1e-8)`);
  } else if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) throw new Error(`${path}: array structure differs`);
    expected.forEach((value, index) => compare(actual[index], value, `${path}[${index}]`));
  } else if (expected !== null && typeof expected === "object") {
    if (actual === null || typeof actual !== "object" ||
        JSON.stringify(Object.keys(actual).sort()) !== JSON.stringify(Object.keys(expected).sort()))
      throw new Error(`${path}: object structure differs`);
    for (const key of Object.keys(expected)) compare(actual[key], expected[key], `${path}.${key}`);
  } else if (actual !== expected) {
    throw new Error(`${path}: expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`);
  }
}
